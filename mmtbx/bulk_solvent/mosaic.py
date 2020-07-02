from __future__ import absolute_import, division, print_function
from cctbx.array_family import flex
from scitbx import matrix
import math
from libtbx import adopt_init_args
import scitbx.lbfgs
from mmtbx.bulk_solvent import kbu_refinery
from cctbx import maptbx
import mmtbx.masks
import boost.python
asu_map_ext = boost.python.import_ext("cctbx_asymmetric_map_ext")
from libtbx import group_args
from mmtbx import bulk_solvent
from mmtbx.ncs import tncs
from collections import OrderedDict
import mmtbx.f_model
import sys
from libtbx.test_utils import approx_equal

from mmtbx import masks
from cctbx.masks import vdw_radii_from_xray_structure
ext = boost.python.import_ext("mmtbx_masks_ext")

# Utilities used by algorithm 2 ------------------------------------------------

class minimizer(object):
  def __init__(self, max_iterations, calculator):
    adopt_init_args(self, locals())
    self.x = self.calculator.x
    self.cntr=0
    self.minimizer = scitbx.lbfgs.run(
      target_evaluator=self,
      termination_params=scitbx.lbfgs.termination_parameters(
        max_iterations=max_iterations))

  def compute_functional_and_gradients(self):
    self.cntr+=1
    self.calculator.update_target_and_grads(x=self.x)
    t = self.calculator.target()
    g = self.calculator.gradients()
    #print "step: %4d"%self.cntr, "target:", t, "params:", \
    #  " ".join(["%10.6f"%i for i in self.x]), math.log(t)
    return t,g

class minimizer2(object):

  def __init__(self, calculator, min_iterations=0, max_iterations=2000):
    adopt_init_args(self, locals())
    self.x = self.calculator.x
    self.n = self.x.size()
    self.cntr=0

  def run(self, use_curvatures=0):
    self.minimizer = kbu_refinery.lbfgs_run(
      target_evaluator=self,
      min_iterations=self.min_iterations,
      max_iterations=self.max_iterations,
      use_curvatures=use_curvatures)
    self(requests_f_and_g=True, requests_diag=False)
    return self

  def __call__(self, requests_f_and_g, requests_diag):
    self.cntr+=1
    self.calculator.update_target_and_grads(x=self.x)
    if (not requests_f_and_g and not requests_diag):
      requests_f_and_g = True
      requests_diag = True
    if (requests_f_and_g):
      self.f = self.calculator.target()
      self.g = self.calculator.gradients()
      self.d = None
    if (requests_diag):
      self.d = self.calculator.curvatures()
      #assert self.d.all_ne(0)
      if(self.d.all_eq(0)): self.d=None
      else:
        self.d = 1 / self.d
    #print "step: %4d"%self.cntr, "target:", self.f, "params:", \
    #  " ".join(["%10.6f"%i for i in self.x]) #, math.log(self.f)
    return self.x, self.f, self.g, self.d

class tg(object):
  def __init__(self, x, i_obs, F, use_curvatures):
    self.x = x
    self.i_obs = i_obs
    self.F = F
    self.t = None
    self.g = None
    self.d = None
    self.sum_i_obs = flex.sum(self.i_obs.data())
    self.use_curvatures=use_curvatures
    self.update_target_and_grads(x=x)

  def update(self, x):
    self.update_target_and_grads(x = x)

  def update_target_and_grads(self, x):
    self.x = x
    s = 1 #180/math.pi
    i_model = flex.double(self.i_obs.data().size(),0)
    for n, kn in enumerate(self.x):
      for m, km in enumerate(self.x):
        tmp = self.F[n].data()*flex.conj(self.F[m].data())
        i_model += kn*km*flex.real(tmp)
        #pn = self.F[n].phases().data()*s
        #pm = self.F[m].phases().data()*s
        #Fn = flex.abs(self.F[n].data())
        #Fm = flex.abs(self.F[m].data())
        #i_model += kn*km*Fn*Fm*flex.cos(pn-pm)
    diff = i_model - self.i_obs.data()
    t = flex.sum(diff*diff)/4
    #
    g = flex.double()
    for j in range(len(self.F)):
      tmp = flex.double(self.i_obs.data().size(),0)
      for m, km in enumerate(self.x):
        tmp += km * flex.real( self.F[j].data()*flex.conj(self.F[m].data()) )
        #pj = self.F[j].phases().data()*s
        #pm = self.F[m].phases().data()*s
        #Fj = flex.abs(self.F[j].data())
        #Fm = flex.abs(self.F[m].data())
        #tmp += km * Fj*Fm*flex.cos(pj-pm)
      g.append(flex.sum(diff*tmp))
    self.t = t
    self.g = g
    #
    if self.use_curvatures:
      d = flex.double()
      for j in range(len(self.F)):
        tmp1 = flex.double(self.i_obs.data().size(),0)
        tmp2 = flex.double(self.i_obs.data().size(),0)
        for m, km in enumerate(self.x):
          zz = flex.real( self.F[j].data()*flex.conj(self.F[m].data()) )
          tmp1 += km * zz
          tmp2 += zz
          #pj = self.F[j].phases().data()*s
          #pm = self.F[m].phases().data()*s
          #Fj = flex.abs(self.F[j].data())
          #Fm = flex.abs(self.F[m].data())
          #tmp += km * Fj*Fm*flex.cos(pj-pm)
        d.append(flex.sum(tmp1*tmp1 + tmp2))
      self.d=d

  def target(self): return self.t/self.sum_i_obs

  def gradients(self): return self.g/self.sum_i_obs

  def gradient(self): return self.gradients()

  def curvatures(self): return self.d/self.sum_i_obs
#-------------------------------------------------------------------------------

def write_map_file(crystal_symmetry, map_data, file_name):
  from iotbx import mrcfile
  mrcfile.write_ccp4_map(
    file_name   = file_name,
    unit_cell   = crystal_symmetry.unit_cell(),
    space_group = crystal_symmetry.space_group(),
    map_data    = map_data,
    labels      = flex.std_string([""]))

class refinery(object):
  def __init__(self, fmodel, fv, alg, log = sys.stdout):
    assert alg in ["alg0","alg2", "alg4"]
    self.log = log
    self.f_calc = fmodel.f_calc()
    self.f_obs  = fmodel.f_obs()
    self.r_free_flags = fmodel.r_free_flags()
    self.F = [self.f_calc.deep_copy()] + fv.keys()

    self.bin_selections = fmodel.bin_selections
    #
    #self._print(fmodel.r_factors(prefix="start: "))
    for it in range(3):
      self._print("cycle: %2d"%it)
      self._print("  volumes: "+" ".join([str(fv[f]) for f in self.F[1:]]))
      f_obs   = self.f_obs.deep_copy()
      k_total = fmodel.k_isotropic()*fmodel.k_anisotropic()*fmodel.scale_k1()
      f_obs   = f_obs.customized_copy(data = self.f_obs.data()/k_total)
      i_obs   = f_obs.customized_copy(data = f_obs.data()*f_obs.data())
      K_MASKS = OrderedDict()


      for i_bin, sel in enumerate(self.bin_selections):
        d_max, d_min = f_obs.select(sel).d_max_min()
        if d_min<3: continue
        bin = "  bin %2d: %5.2f-%-5.2f: "%(i_bin, d_max, d_min)
        F = [f.select(sel) for f in self.F]
        # algorithm_0
        if(alg=="alg0"):
          k_masks = algorithm_0(
            f_obs = f_obs.select(sel),
            F     = F)
        # algorithm_4
        if(alg=="alg4"):
          k_masks = algorithm_4(
            f_obs             = f_obs.select(sel),
            F                 = F,
            auto_converge_eps = 0.0001)
        # algorithm_2
        if(alg=="alg2"):
          k_masks = algorithm_2(
            i_obs          = i_obs.select(sel),
            F              = F,
            x              = self._get_x_init(i_bin),
            use_curvatures = False)
        self._print(bin+" ".join(["%6.2f"%k for k in k_masks]))
        K_MASKS[sel] = k_masks
      #
      f_calc_data = self.f_calc.data().deep_copy()
      f_bulk_data = flex.complex_double(fmodel.f_calc().data().size(), 0)
      for sel, k_masks in zip(K_MASKS.keys(), K_MASKS.values()):
        f_bulk_data_ = flex.complex_double(sel.count(True), 0)
        for i_mask, k_mask in enumerate(k_masks):
          if i_mask==0:
            f_calc_data = f_calc_data.set_selected(sel,
              f_calc_data.select(sel)*k_mask)
            continue
          f_bulk_data_ += self.F[i_mask].data().select(sel)*k_mask
        f_bulk_data = f_bulk_data.set_selected(sel,f_bulk_data_)
      #
      self.update_F(K_MASKS)
      f_bulk = fmodel.f_calc().customized_copy(data = f_bulk_data)
      self.fmodel = mmtbx.f_model.manager(
        f_obs          = self.f_obs,
        r_free_flags   = self.r_free_flags,
        f_calc         = self.f_obs.customized_copy(data = f_calc_data),
        #f_mask         = fmodel.f_masks()[0],#f_bulk,
        bin_selections=self.bin_selections,
        f_mask         = f_bulk,
        k_mask         = flex.double(f_obs.data().size(),1)
        )


      #self.fmodel = mmtbx.f_model.manager(
      #  f_obs          = self.f_obs,
      #  r_free_flags   = self.r_free_flags,
      #  f_calc         = self.f_obs.customized_copy(data = f_calc_data+f_bulk_data),
      #  f_mask         = fmodel.f_masks()[0],#f_bulk,
      #  bin_selections=self.bin_selections,
      #  #f_mask         = f_bulk,
      #  k_mask         = flex.double(f_obs.data().size(),1)
      #  )



      #
      self.fmodel.update_all_scales(remove_outliers=False)


      #self._print(self.fmodel.r_factors(prefix="  "))
      self.mc = self.fmodel.electron_density_map().map_coefficients(
        map_type   = "mFobs-DFmodel",
        isotropize = True,
        exclude_free_r_reflections = False)


  def _print(self, m):
    if(self.log is not None):
      print(m, file=self.log)

  def update_F(self, K_MASKS):
    tmp = []
    for i_mask, F in enumerate(self.F):
      k_masks = [k_masks_bin[i_mask] for k_masks_bin in K_MASKS.values()]
      if(i_mask == 0):      tmp.append(self.F[0])
      elif k_masks[0]>=0.1: tmp.append(F)
      self.F = tmp[:]

  def _get_x_init(self, i_bin):
    k_maks1_init = 0.35 - i_bin*0.35/len(self.bin_selections)
    x = flex.double([1,k_maks1_init])
    x.extend( flex.double(len(self.F)-2, 0.1))
    return x

def get_f_mask(xrs, ma, step):
  crystal_gridding = maptbx.crystal_gridding(
    unit_cell        = xrs.unit_cell(),
    space_group_info = xrs.space_group_info(),
    symmetry_flags   = maptbx.use_space_group_symmetry,
    step             = step)
  n_real = crystal_gridding.n_real()
  atom_radii = vdw_radii_from_xray_structure(xray_structure = xrs)
  mask_params = masks.mask_master_params.extract()
  grid_step_factor = ma.d_min()/step
#  # 1
#  asu_mask = ext.atom_mask(
#    unit_cell                = xrs.unit_cell(),
#    group                    = xrs.space_group(),
#    resolution               = ma.d_min(),
#    grid_step_factor         = grid_step_factor,
#    solvent_radius           = mask_params.solvent_radius,
#    shrink_truncation_radius = mask_params.shrink_truncation_radius)
#  asu_mask.compute(xrs.sites_frac(), atom_radii)
#  fm_asu = asu_mask.structure_factors(ma.indices())
#  f_mask_1 = ma.set().array(data = fm_asu)
#  print (asu_mask.grid_size())
  # 2
  asu_mask = ext.atom_mask(
    unit_cell                = xrs.unit_cell(),
    space_group              = xrs.space_group(),
    gridding_n_real          = n_real,
    solvent_radius           = mask_params.solvent_radius,
    shrink_truncation_radius = mask_params.shrink_truncation_radius)
  asu_mask.compute(xrs.sites_frac(), atom_radii)
  fm_asu = asu_mask.structure_factors(ma.indices())
  f_mask_2 = ma.set().array(data = fm_asu)
#  # 3
#  mask_params.grid_step_factor = grid_step_factor
#  mask_manager = masks.manager(
#    miller_array      = ma,
#    miller_array_twin = None,
#    mask_params       = mask_params)
#  f_mask_3 = mask_manager.shell_f_masks(xray_structure=xrs, force_update=True)[0]
#  # 4
#  mask_p1 = mmtbx.masks.mask_from_xray_structure(
#    xray_structure        = xrs,
#    p1                    = True,
#    for_structure_factors = True,
#    n_real                = n_real,
#    in_asu                = False).mask_data
#  maptbx.unpad_in_place(map=mask_p1)
#  mask = asu_map_ext.asymmetric_map(
#    xrs.crystal_symmetry().space_group().type(), mask_p1).data()
#  f_mask_4 = ma.structure_factors_from_asu_map(
#    asu_map_data = mask, n_real = n_real)
#  ##
#  print (flex.mean(abs(f_mask_1).data()))
#  print (flex.mean(abs(f_mask_2).data()))
#  print (flex.mean(abs(f_mask_3).data()))
#  print (flex.mean(abs(f_mask_4).data()))
#  STOP()
#  assert approx_equal(f_mask_1.data(), f_mask_2.data())
#  assert approx_equal(f_mask_1.data(), f_mask_3.data())
#  assert approx_equal(f_mask_1.data(), f_mask_4.data())
  return f_mask_2

class mosaic_f_mask(object):
  def __init__(self,
               miller_array,
               xray_structure,
               step,
               volume_cutoff,
               log = sys.stdout,
               f_obs=None,
               r_free_flags=None,
               f_calc=None,
               write_masks=False):
    adopt_init_args(self, locals())
    assert [f_obs, f_calc, r_free_flags].count(None) in [0,3]
    self.crystal_symmetry = self.xray_structure.crystal_symmetry()
    # compute mask in p1 (via ASU)
    self.crystal_gridding = maptbx.crystal_gridding(
      unit_cell        = xray_structure.unit_cell(),
      space_group_info = xray_structure.space_group_info(),
      symmetry_flags   = maptbx.use_space_group_symmetry,
      step             = step)
    self.n_real = self.crystal_gridding.n_real()
    # XXX Where do we want to deal with H and occ==0?
    mask_p1 = mmtbx.masks.mask_from_xray_structure(
      xray_structure        = xray_structure,
      p1                    = True,
      for_structure_factors = True,
      n_real                = self.n_real,
      in_asu                = False).mask_data
    maptbx.unpad_in_place(map=mask_p1)
    self.solvent_content = 100.*mask_p1.count(1)/mask_p1.size()
    if(write_masks):
      write_map_file(crystal_symmetry=xray_structure.crystal_symmetry(),
        map_data=mask_p1, file_name="mask_whole.mrc")
    # conn analysis
    co = maptbx.connectivity(
      map_data                   = mask_p1,
      threshold                  = 0.01,
      preprocess_against_shallow = True,
      wrapping                   = True)
    co.merge_symmetry_related_regions(space_group=xray_structure.space_group())
    del mask_p1
    self.conn = co.result().as_double()
    z = zip(co.regions(),range(0,co.regions().size()))
    sorted_by_volume = sorted(z, key=lambda x: x[0], reverse=True)
    f_mask_data_0 = flex.complex_double(miller_array.data().size(), 0)
    FM = OrderedDict()
    self.FV = OrderedDict()
    self.mc = None
    diff_map = None
    mean_diff_map = None
    self.regions = OrderedDict()
    print("   volume_p1    uc(%)   volume_asu  id   mFo-DFc: min,max,mean,sd",
      file=log)
    for i_seq, p in enumerate(sorted_by_volume):
      v, i = p
      # skip macromolecule
      if(i==0): continue
      # skip small volume
      volume = v*step**3
      uc_fraction = v*100./self.conn.size()
      if(volume_cutoff is not None):
        if volume < volume_cutoff: continue

      selection = self.conn==i
      mask_i_asu = self.compute_i_mask_asu(selection=selection, volume=volume)
      volume_asu = (mask_i_asu>0).count(True)*step**3

      if(i_seq==1 or uc_fraction>5):
        f_mask_i = miller_array.structure_factors_from_asu_map(
          asu_map_data = mask_i_asu, n_real = self.n_real)
        f_mask_data_0 += f_mask_i.data()

      if(uc_fraction < 5 and diff_map is None):
        diff_map = self.compute_diff_map(f_mask_data = f_mask_data_0)

      mi,ma,me,sd = None,None,None,None
      if(diff_map is not None):
        blob = diff_map.select(selection.iselection())
        mean_diff_map = flex.mean(diff_map.select(selection.iselection()))
        mi,ma,me = flex.min(blob), flex.max(blob), flex.mean(blob)
        sd = blob.sample_standard_deviation()

      print("%12.3f"%volume, "%8.4f"%round(uc_fraction,4),
            "%12.3f"%volume_asu, "%3d"%i,
            "%7s"%str(None) if diff_map is None else "%7.3f %7.3f %7.3f %7.3f"%(
              mi,ma,me,sd), file=log)

      if(mean_diff_map is not None and mean_diff_map<=0): continue

      self.regions[i_seq] = group_args(
        id          = i,
        i_seq       = i_seq,
        volume      = volume,
        uc_fraction = uc_fraction,
        diff_map    = group_args(mi=mi, ma=ma, me=me, sd=sd))

      if(not(i_seq==1 or uc_fraction>5)):
        f_mask_i = miller_array.structure_factors_from_asu_map(
          asu_map_data = mask_i_asu, n_real = self.n_real)

      FM.setdefault(round(volume, 3), []).append(f_mask_i.data())
      self.FV[f_mask_i] = round(volume, 3)
    #
    f_mask_0 = miller_array.customized_copy(data = f_mask_data_0)
    #
    self.f_mask_0  = f_mask_0
    self.do_mosaic = False
    if(len(self.FV.keys())>1):
      self.do_mosaic = True

  def compute_diff_map(self, f_mask_data):
    if(self.f_obs is None): return None
    f_mask = self.f_obs.customized_copy(data = f_mask_data)
    fmodel = mmtbx.f_model.manager(
      f_obs        = self.f_obs,
      r_free_flags = self.r_free_flags,
      f_calc       = self.f_calc,
      f_mask       = f_mask)
    fmodel.update_all_scales(remove_outliers=True)
    self.mc = fmodel.electron_density_map().map_coefficients(
      map_type   = "mFobs-DFmodel",
      isotropize = True,
      exclude_free_r_reflections = False)
    fft_map = self.mc.fft_map(crystal_gridding = self.crystal_gridding)
    fft_map.apply_sigma_scaling()
    return fft_map.real_map_unpadded()

  def compute_i_mask_asu(self, selection, volume):
    mask_i = flex.double(flex.grid(self.n_real), 0)
    mask_i = mask_i.set_selected(selection, 1)
    if(self.write_masks):
      write_map_file(
        crystal_symmetry = self.crystal_symmetry,
        map_data         = mask_i,
        file_name        = "mask_%s.mrc"%str(round(volume,3)))
    tmp = asu_map_ext.asymmetric_map(
      self.crystal_symmetry.space_group().type(), mask_i).data()
    return tmp

def algorithm_0(f_obs, F):
  """
  Grid search
  """
  fc, f_masks = F[0], F[1:]
  k_mask_trial_range=[]
  s = 0
  while s<0.4:
    k_mask_trial_range.append(s)
    s+=0.001
  r = []
  fc_data = fc.data()
  for i, f_mask in enumerate(f_masks):
    #print("mask ",i)
    assert f_obs.data().size() == fc.data().size()
    assert f_mask.data().size() == fc.data().size()
    #print (bulk_solvent.r_factor(f_obs.data(),fc_data))
    kmask_, k_ = \
      bulk_solvent.k_mask_and_k_overall_grid_search(
        f_obs.data(),
        fc_data,
        f_mask.data(),
        flex.double(k_mask_trial_range),
        flex.bool(fc.data().size(),True))
    r.append(kmask_)
    fc_data += fc_data*k_ + kmask_*f_mask.data()
    #print (bulk_solvent.r_factor(f_obs.data(),fc_data + kmask_*f_mask.data(),k_))
  r = [1,]+r
  return r

def algorithm_2(i_obs, F, x, use_curvatures=True, macro_cycles=10):
  """
  Unphased one-step search
  """
  calculator = tg(i_obs = i_obs, F=F, x = x, use_curvatures=use_curvatures)
  for it in range(macro_cycles):
    if(use_curvatures):
      m = minimizer(max_iterations=100, calculator=calculator)
    else:
      #upper = flex.double([10] + [5]*(x.size()-1))
      #lower = flex.double([0.1] + [-5]*(x.size()-1))
      upper = flex.double([10] + [0.65]*(x.size()-1))
      lower = flex.double([0.1] + [0]*(x.size()-1))

      #upper = flex.double([1] + [0.65]*(x.size()-1))
      #lower = flex.double([1] + [0]*(x.size()-1))
      #upper = flex.double([1] + [5.65]*(x.size()-1))
      #lower = flex.double([1] + [-5]*(x.size()-1))
      m = tncs.minimizer(
        potential       = calculator,
        use_bounds      = 2,
        lower_bound     = lower,
        upper_bound     = upper,
        initial_values  = x).run()
    calculator = tg(i_obs = i_obs, F=F, x = m.x, use_curvatures=use_curvatures)
  if(use_curvatures):
    for it in range(10):
      m = minimizer(max_iterations=100, calculator=calculator)
      calculator = tg(i_obs = i_obs, F=F, x = m.x, use_curvatures=use_curvatures)
      m = minimizer2(max_iterations=100, calculator=calculator).run(use_curvatures=True)
      calculator = tg(i_obs = i_obs, F=F, x = m.x, use_curvatures=use_curvatures)
  return m.x

def algorithm_3(i_obs, fc, f_masks):
  """
  Unphased two-step search
  """
  F = [fc]+f_masks
  Gnm = []
  cs = {}
  cntr=0
  nm=[]
  # Compute and store Gnm
  for n, Fn in enumerate(F):
    for m, Fm in enumerate(F):
      if m < n:
        continue
      Gnm.append( flex.real( Fn.data()*flex.conj(Fm.data()) ) )
      cs[(n,m)] = cntr
      cntr+=1
      nm.append((n,m))
  # Keep track of indices for "upper triangular matrix vs full"
  for k,v in zip(cs.keys(), cs.values()):
    i,j=k
    if i==j: continue
    else: cs[(j,i)]=v
  # Generate and solve system Ax=b, x = A_1*b
  A = []
  b = []
  for u, Gnm_u in enumerate(Gnm):
    for v, Gnm_v in enumerate(Gnm):
      scale = 2
      n,m=nm[v]
      if n==m: scale=1
      A.append( flex.sum(Gnm_u*Gnm_v)*scale )
    b.append( flex.sum(Gnm_u * i_obs.data()) )
  A = matrix.sqr(A)
  A_1 = A.inverse()
  b = matrix.col(b)
  x = A_1 * b
  # Expand Xmn from solution x
  Xmn = []
  for n, Fn in enumerate(F):
    rows = []
    for m, Fm in enumerate(F):
      x_ = x[cs[(n,m)]]
      rows.append(x_)
    Xmn.append(rows)
  # Do formula (19)
  lnK = []
  for j, Fj in enumerate(F):
    t1 = flex.sum( flex.log( flex.double(Xmn[j]) ) )
    t2 = 0
    for n, Fn in enumerate(F):
      for m, Fm in enumerate(F):
        t2 += math.log(Xmn[n][m])
    t2 = t2 / (2*len(F))
    lnK.append( 1/len(F)*(t1-t2) )
  return [math.exp(x) for x in lnK]

def algorithm_4(f_obs, F, max_cycles=100, auto_converge_eps=1.e-7):
  """
  Phased simultaneous search
  """
  fc, f_masks = F[0], F[1:]
  fc = fc.deep_copy()
  F = [fc]+F[1:]
  x_res = None
  cntr = 0
  x_prev = None
  while True:
    f_obs_cmpl = f_obs.phase_transfer(phase_source=fc)
    A = []
    b = []
    for j, Fj in enumerate(F):
      A_rows = []
      for n, Fn in enumerate(F):
        Gjn = flex.real( Fj.data()*flex.conj(Fn.data()) )
        A_rows.append( flex.sum(Gjn) )
      Hj = flex.real( Fj.data()*flex.conj(f_obs_cmpl.data()) )
      b.append(flex.sum(Hj))
      A.extend(A_rows)
    A = matrix.sqr(A)
    A_1 = A.inverse()
    b = matrix.col(b)
    x = A_1 * b
    if x_res is None: x_res  = flex.double(x)
    else:             x_res += flex.double(x)
    x_ = [x[0]] + list(x_res[1:])
    #print("iteration:", cntr, " ".join(["%10.6f"%i for i in x_]))
    #
    fc_d = fc.data()
    for i, f in enumerate(F):
      if i == 0: continue
      fc_d += x[i]*f.data()
    fc = fc.customized_copy(data = fc_d)
    cntr+=1
    if(cntr>max_cycles): break
    if(x_prev is None): x_prev = x_[:]
    else:
      max_diff = flex.max(flex.abs(flex.double(x_prev)-flex.double(x_)))
      if(max_diff<=auto_converge_eps): break
      x_prev = x_[:]
  return x_
