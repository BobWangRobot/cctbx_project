from cctbx.development import random_structure
from cctbx.development import debug_utils
from cctbx import xray
from cctbx import maptbx
from cctbx import miller
from cctbx import crystal
from cctbx import adptbx
from cctbx.array_family import flex
from scitbx.python_utils.misc import adopt_init_args, user_plus_sys_time
from scitbx.test_utils import approx_equal
from scitbx import fftpack
import random
import sys

class resampling(crystal.symmetry):

  def __init__(self, miller_set=None,
                     crystal_symmetry=None,
                     d_min=None,
                     grid_resolution_factor=1/3.,
                     symmetry_flags=maptbx.use_space_group_symmetry,
                     mandatory_grid_factors=None,
                     quality_factor=100000, u_extra=None, b_extra=None,
                     wing_cutoff=1.e-10,
                     exp_table_one_over_step_size=-100,
                     max_prime=5):
    assert miller_set is None or crystal_symmetry is None
    assert [quality_factor, u_extra, b_extra].count(None) == 2
    if (miller_set is None):
      assert crystal_symmetry is not None and d_min is not None
    else:
      crystal_symmetry = miller_set
      if (d_min is None):
        d_min = miller_set.d_min()
      else:
        assert d_min <= miller_set.d_min()
    crystal.symmetry._copy_constructor(self, crystal_symmetry)
    quality_factor = xray.structure_factors.quality_factor_from_any(
      d_min, grid_resolution_factor, quality_factor, u_extra, b_extra)
    del miller_set
    del u_extra
    del b_extra
    adopt_init_args(self, locals(), hide=0001)
    self._crystal_gridding = None
    self._crystal_gridding_tags = None
    self._rfft = None
    self._u_extra = None

  def d_min(self):
    return self._d_min

  def grid_resolution_factor(self):
    return self._grid_resolution_factor

  def symmetry_flags(self):
    return self._symmetry_flags

  def mandatory_grid_factors(self):
    return self._mandatory_grid_factors

  def quality_factor(self):
    return self._quality_factor

  def wing_cutoff(self):
    return self._wing_cutoff

  def exp_table_one_over_step_size(self):
    return self._exp_table_one_over_step_size

  def max_prime(self):
    return self._max_prime

  def crystal_gridding(self, assert_shannon_sampling=0001):
    if (self._crystal_gridding is None):
      self._crystal_gridding = maptbx.crystal_gridding(
        unit_cell=self.unit_cell(),
        d_min=self.d_min(),
        resolution_factor=self.grid_resolution_factor(),
        symmetry_flags=self.symmetry_flags(),
        space_group_info=self.space_group_info(),
        mandatory_factors=self.mandatory_grid_factors(),
        max_prime=self.max_prime(),
        assert_shannon_sampling=assert_shannon_sampling)
    return self._crystal_gridding

  def crystal_gridding_tags(self, assert_shannon_sampling=0001):
    if (self._crystal_gridding_tags is None):
      self._crystal_gridding_tags = self.crystal_gridding(
        assert_shannon_sampling).tags()
    return self._crystal_gridding_tags

  def rfft(self):
    if (self._rfft is None):
      self._rfft = fftpack.real_to_complex_3d(self.crystal_gridding().n_real())
    return self._rfft

  def u_extra(self):
    if (self._u_extra is None):
      self._u_extra = xray.calc_u_extra(
        self.d_min(),
        self.grid_resolution_factor(),
        self.quality_factor())
    return self._u_extra

  def setup_fft(self):
    self.crystal_gridding_tags()
    self.rfft()
    self.u_extra()
    return self

  def ft_dp(self, dp):
    n = self.rfft().n_real()
    norm = self.unit_cell().volume()/(n[0]*n[1]*n[2])
    dpe = dp.deep_copy()
    xray.eliminate_u_extra(
      self.unit_cell(),
      self.u_extra(),
      dpe.indices(),
      dpe.data(),
      norm)
    dpe = miller.array(dpe, dpe.data() \
                            * flex.polar(dpe.epsilons().data().as_double(),0))
    return miller.fft_map(
      crystal_gridding=self.crystal_gridding(),
      fourier_coefficients=dpe)

  def __call__(self, xray_structure,
                     dp,
                     d_target_d_f_calc=None,
                     gradient_flags=None,
                     electron_density_must_be_positive=0001,
                     verbose=0):
    assert not gradient_flags is None
    r = random.random()
    if (r > 2/3.):
      gradient_flags = xray.structure_factors.gradient_flags(default=0001)
    elif (r > 1/3.):
      gradient_flags = gradient_flags.copy()
      if (random.random() > 0.5): gradient_flags.site = 0001
      if (random.random() > 0.5): gradient_flags.u_iso = 0001
      if (random.random() > 0.5): gradient_flags.u_aniso = 0001
      if (random.random() > 0.5): gradient_flags.occupancy = 0001
      if (random.random() > 0.5): gradient_flags.fp = 0001
      if (random.random() > 0.5): gradient_flags.fdp = 0001
    self.setup_fft()
    cmap = self.ft_dp(dp).complex_map()
    assert not cmap.is_padded()
    if (0 or verbose):
      gradient_flags.show_summary()
      print "grid:", cmap.focus()
      print "ft_dt_map real: %.4g %.4g" % (
        flex.min(flex.real(cmap)), flex.max(flex.real(cmap)))
      print "ft_dt_map imag: %.4g %.4g" % (
        flex.min(flex.imag(cmap)), flex.max(flex.imag(cmap)))
      print
    time_sampling = user_plus_sys_time()
    result = xray.fast_gradients(
      xray_structure.unit_cell(),
      xray_structure.scatterers(),
      cmap,
      gradient_flags,
      self.u_extra(),
      self.wing_cutoff(),
      self.exp_table_one_over_step_size(),
      electron_density_must_be_positive)
    time_sampling = time_sampling.elapsed()
    if (0 or verbose):
      print "max_shell_radii:", result.max_shell_radii()
      print "exp_table_size:", result.exp_table_size()
      print
    return result

class judge:

  def __init__(self, scatterer, label, reference, other, top):
    label += [" iso", " aniso"][int(scatterer.anisotropic_flag)]
    s = ""
    r = (reference-other)/top
    s += " %.5f " % r + label
    self.is_bad = 00000
    if (abs(r) > 0.03):
      s += " very large mismatch"
      self.is_bad = 0001
    elif (abs(r) > 0.01):
      s += " large mismatch"
    self.s = s.lstrip()

  def __str__(self):
    return self.s

class shifted_site:

  def __init__(self, f_obs, structure, i_scatterer, i_xyz, shift):
    self.structure_shifted = structure.deep_copy_scatterers()
    site = list(self.structure_shifted.scatterers()[i_scatterer].site)
    site[i_xyz] += shift
    self.structure_shifted.scatterers()[i_scatterer].site = site
    self.f_calc = f_obs.structure_factors_from_scatterers(
      xray_structure=self.structure_shifted, direct=0001).f_calc()

def site(structure_ideal, d_min, f_obs, verbose=0):
  sh = shifted_site(f_obs, structure_ideal, 0, 0, 0.01)
  if (0 or verbose):
    print "site"
    sh.structure_shifted.show_summary().show_scatterers()
    print
  ls = xray.targets_least_squares_residual(
    f_obs.data(), sh.f_calc.data(), 0001, 1)
  sfd = xray.structure_factors.from_scatterers_direct(
    xray_structure=sh.structure_shifted,
    miller_set=f_obs,
    d_target_d_f_calc=ls.derivatives(),
    gradient_flags=xray.structure_factors.gradient_flags(site=0001))
  re = resampling(miller_set=f_obs)
  dp0 = miller.array(miller_set=f_obs, data=ls.derivatives())
  map0 = re(
    xray_structure=sh.structure_shifted,
    dp=dp0,
    gradient_flags=xray.structure_factors.gradient_flags(site=0001),
    verbose=verbose)
  sfd.d_target_d_site_inplace_frac_as_cart(sfd.d_target_d_site())
  sfd.d_target_d_site_inplace_frac_as_cart(map0.d_target_d_site())
  top_gradient = None
  for i_scatterer in (0,1,2):
    scatterer = sh.structure_shifted.scatterers()[i_scatterer]
    for i_xyz in (0,1,2):
      direct_summ = sfd.d_target_d_site()[i_scatterer][i_xyz]
      if (top_gradient is None): top_gradient = direct_summ
      fast_gradie = map0.d_target_d_site()[i_scatterer][i_xyz] \
                  * f_obs.space_group().n_ltr()
      match = judge(scatterer, "site", direct_summ, fast_gradie, top_gradient)
      if (0 or verbose):
        print "direct summ[%d][%d]: " % (i_scatterer,i_xyz), direct_summ
        print "fast gradie[%d][%d]: " % (i_scatterer,i_xyz), fast_gradie, match
        print
      assert not match.is_bad
  sys.stdout.flush()

class shifted_u_iso:

  def __init__(self, f_obs, structure, i_scatterer, shift):
    self.structure_shifted = structure.deep_copy_scatterers()
    self.structure_shifted.scatterers()[i_scatterer].u_iso += shift
    self.f_calc = f_obs.structure_factors_from_scatterers(
      xray_structure=self.structure_shifted).f_calc()

def u_iso(structure_ideal, d_min, f_obs, verbose=0):
  sh = shifted_u_iso(f_obs, structure_ideal, 0, 0.05)
  if (0 or verbose):
    print "u_iso"
    sh.structure_shifted.show_summary().show_scatterers()
    print
  ls = xray.targets_least_squares_residual(
    f_obs.data(), sh.f_calc.data(), 0001, 1)
  sfd = xray.structure_factors.from_scatterers_direct(
    xray_structure=sh.structure_shifted,
    miller_set=f_obs,
    d_target_d_f_calc=ls.derivatives(),
    gradient_flags=xray.structure_factors.gradient_flags(u_iso=0001))
  re = resampling(miller_set=f_obs)
  dp0 = miller.array(miller_set=f_obs, data=ls.derivatives())
  map0 = re(
    xray_structure=sh.structure_shifted,
    dp=dp0,
    gradient_flags=xray.structure_factors.gradient_flags(u_iso=0001),
    verbose=verbose)
  top_gradient = None
  for i_scatterer in (0,1,2):
    scatterer = sh.structure_shifted.scatterers()[i_scatterer]
    direct_summ = sfd.d_target_d_u_iso()[i_scatterer]
    if (top_gradient is None): top_gradient = direct_summ
    fast_gradie = map0.d_target_d_u_iso()[i_scatterer] \
                * f_obs.space_group().n_ltr()
    match = judge(scatterer, "u_iso", direct_summ, fast_gradie, top_gradient)
    if (0 or verbose):
      print "direct summ[%d]: " % i_scatterer, direct_summ
      print "fast gradie[%d]: " % i_scatterer, fast_gradie, match
      print
    assert not match.is_bad
  sys.stdout.flush()

class shifted_u_star:

  def __init__(self, f_obs, structure, i_scatterer, ij, shift):
    self.structure_shifted = structure.deep_copy_scatterers()
    scatterer = self.structure_shifted.scatterers()[i_scatterer]
    u_star = list(scatterer.u_star)
    u_star[ij] += shift
    scatterer.u_star = u_star
    self.f_calc = f_obs.structure_factors_from_scatterers(
      xray_structure=self.structure_shifted).f_calc()

def ij_product(hkl, ij):
  if (ij < 3): return hkl[ij]**2
  if (ij == 3): return 2*hkl[0]*hkl[1]
  if (ij == 4): return 2*hkl[0]*hkl[2]
  if (ij == 5): return 2*hkl[1]*hkl[2]
  raise RuntimeError

def u_star(structure_ideal, d_min, f_obs, verbose=0):
  sh = shifted_u_star(f_obs, structure_ideal, 0, 0, 0.0001)
  if (0 or verbose):
    print "u_star"
    sh.structure_shifted.show_summary().show_scatterers()
    print
  ls = xray.targets_least_squares_residual(
    f_obs.data(), sh.f_calc.data(), 0001, 1)
  sfd = xray.structure_factors.from_scatterers_direct(
    xray_structure=sh.structure_shifted,
    miller_set=f_obs,
    d_target_d_f_calc=ls.derivatives(),
    gradient_flags=xray.structure_factors.gradient_flags(u_aniso=0001))
  re = resampling(miller_set=f_obs)
  dp0 = miller.array(miller_set=f_obs, data=ls.derivatives())
  map0 = re(
    xray_structure=sh.structure_shifted,
    dp=dp0,
    gradient_flags=xray.structure_factors.gradient_flags(u_aniso=0001),
    verbose=verbose)
  top_gradient = None
  for i_scatterer in (0,1,2):
    scatterer = sh.structure_shifted.scatterers()[i_scatterer]
    sfd_star = sfd.d_target_d_u_star()[i_scatterer]
    sfd_cart = adptbx.grad_u_star_as_u_cart(
      structure_ideal.unit_cell(), sfd_star)
    assert approx_equal(
      sfd_star,
      adptbx.grad_u_cart_as_u_star(structure_ideal.unit_cell(), sfd_cart))
    for ij in xrange(6):
      direct_summ = sfd.d_target_d_u_star()[i_scatterer][ij]
      if (top_gradient is None): top_gradient = direct_summ
      fast_gradie = map0.d_target_d_u_star()[i_scatterer][ij] \
                  * f_obs.space_group().n_ltr()
      match = judge(scatterer, "u_star", direct_summ,fast_gradie,top_gradient)
      if (0 or verbose):
        print "direct summ[%d][%d]: " % (i_scatterer, ij), direct_summ
        print "fast gradie[%d][%d]: " % (i_scatterer, ij), fast_gradie, match
        print
      assert not match.is_bad
  sys.stdout.flush()

class shifted_occupancy:

  def __init__(self, f_obs, structure, i_scatterer, shift):
    self.structure_shifted = structure.deep_copy_scatterers()
    self.structure_shifted.shift_occupancy(i_scatterer, shift)
    self.f_calc = f_obs.structure_factors_from_scatterers(
      xray_structure=self.structure_shifted).f_calc()

def occupancy(structure_ideal, d_min, f_obs, verbose=0):
  sh = shifted_occupancy(f_obs, structure_ideal, 0, 0.2)
  if (0 or verbose):
    print "occupancy"
    sh.structure_shifted.show_summary().show_scatterers()
    print
  ls = xray.targets_least_squares_residual(
    f_obs.data(), sh.f_calc.data(), 0001, 1)
  sfd = xray.structure_factors.from_scatterers_direct(
    xray_structure=sh.structure_shifted,
    miller_set=f_obs,
    d_target_d_f_calc=ls.derivatives(),
    gradient_flags=xray.structure_factors.gradient_flags(occupancy=0001))
  re = resampling(miller_set=f_obs)
  dp0 = miller.array(miller_set=f_obs, data=ls.derivatives())
  map0 = re(
    xray_structure=sh.structure_shifted,
    dp=dp0,
    gradient_flags=xray.structure_factors.gradient_flags(occupancy=0001),
    verbose=verbose)
  top_gradient = None
  for i_scatterer in (0,1,2):
    scatterer = sh.structure_shifted.scatterers()[i_scatterer]
    direct_summ = sfd.d_target_d_occupancy()[i_scatterer]
    if (top_gradient is None): top_gradient = direct_summ
    fast_gradie = map0.d_target_d_occupancy()[i_scatterer] \
                * f_obs.space_group().n_ltr()
    match = judge(scatterer, "occupancy", direct_summ,fast_gradie,top_gradient)
    if (0 or verbose):
      print "direct summ[%d]: " % i_scatterer, direct_summ
      print "fast gradie[%d]: " % i_scatterer, fast_gradie, match
      print
    assert not match.is_bad
  sys.stdout.flush()

class shifted_fp:

  def __init__(self, f_obs, structure, i_scatterer, shift):
    self.structure_shifted = structure.deep_copy_scatterers()
    self.structure_shifted.scatterers()[i_scatterer].fp_fdp += shift
    self.f_calc = f_obs.structure_factors_from_scatterers(
      xray_structure=self.structure_shifted).f_calc()

def fp(structure_ideal, d_min, f_obs, verbose=0):
  sh = shifted_fp(f_obs, structure_ideal, 0, -0.2)
  if (0 or verbose):
    print "fp"
    sh.structure_shifted.show_summary().show_scatterers()
    print
  ls = xray.targets_least_squares_residual(
    f_obs.data(), sh.f_calc.data(), 0001, 1)
  sfd = xray.structure_factors.from_scatterers_direct(
    xray_structure=sh.structure_shifted,
    miller_set=f_obs,
    d_target_d_f_calc=ls.derivatives(),
    gradient_flags=xray.structure_factors.gradient_flags(fp=0001))
  re = resampling(miller_set=f_obs)
  dp0 = miller.array(miller_set=f_obs, data=ls.derivatives())
  map0 = re(
    xray_structure=sh.structure_shifted,
    dp=dp0,
    gradient_flags=xray.structure_factors.gradient_flags(fp=0001),
    verbose=verbose)
  top_gradient = None
  for i_scatterer in (0,1,2):
    scatterer = sh.structure_shifted.scatterers()[i_scatterer]
    direct_summ = sfd.d_target_d_fp()[i_scatterer]
    if (top_gradient is None): top_gradient = direct_summ
    fast_gradie = map0.d_target_d_fp()[i_scatterer] \
                * f_obs.space_group().n_ltr()
    match = judge(scatterer, "fp", direct_summ, fast_gradie, top_gradient)
    if (0 or verbose):
      print "direct summ[%d]: " % i_scatterer, direct_summ
      print "fast gradie[%d]: " % i_scatterer, fast_gradie, match
      print
    assert not match.is_bad
  sys.stdout.flush()

class shifted_fdp:

  def __init__(self, f_obs, structure, i_scatterer, shift):
    self.structure_shifted = structure.deep_copy_scatterers()
    self.structure_shifted.scatterers()[i_scatterer].fp_fdp += complex(0,shift)
    self.f_calc = f_obs.structure_factors_from_scatterers(
      xray_structure=self.structure_shifted).f_calc()

def fdp(structure_ideal, d_min, f_obs, verbose=0):
  sh = shifted_fdp(f_obs, structure_ideal, 0, 2)
  if (0 or verbose):
    print "fdp"
    sh.structure_shifted.show_summary().show_scatterers()
    print
  ls = xray.targets_least_squares_residual(
    f_obs.data(), sh.f_calc.data(), 0001, 1)
  sfd = xray.structure_factors.from_scatterers_direct(
    xray_structure=sh.structure_shifted,
    miller_set=f_obs,
    d_target_d_f_calc=ls.derivatives(),
    gradient_flags=xray.structure_factors.gradient_flags(fdp=0001))
  re = resampling(miller_set=f_obs)
  dp0 = miller.array(miller_set=f_obs, data=ls.derivatives())
  map0 = re(
    xray_structure=sh.structure_shifted,
    dp=dp0,
    gradient_flags=xray.structure_factors.gradient_flags(fdp=0001),
    verbose=verbose)
  top_gradient = None
  for i_scatterer in (0,1,2):
    scatterer = sh.structure_shifted.scatterers()[i_scatterer]
    direct_summ = sfd.d_target_d_fdp()[i_scatterer]
    if (top_gradient is None): top_gradient = direct_summ
    fast_gradie = map0.d_target_d_fdp()[i_scatterer] \
                * f_obs.space_group().n_ltr()
    match = judge(scatterer, "fdp", direct_summ, fast_gradie, top_gradient)
    if (0 or verbose):
      print "direct summ[%d]: " % i_scatterer, direct_summ
      print "fast gradie[%d]: " % i_scatterer, fast_gradie, match
      print
    assert not match.is_bad
  sys.stdout.flush()

def run_one(space_group_info, n_elements=3, volume_per_atom=1000, d_min=2,
            fdp_flag=0, anisotropic_flag=0, verbose=0):
  structure_ideal = random_structure.xray_structure(
    space_group_info,
    elements=("Se",)*n_elements,
    volume_per_atom=volume_per_atom,
    min_distance=5,
    general_positions_only=1,
    random_f_prime_d_min=d_min-1,
    random_f_prime_scale=0.6,
    random_f_double_prime=fdp_flag,
    anisotropic_flag=anisotropic_flag,
    random_u_iso=0001,
    random_u_iso_scale=.3,
    random_u_cart_scale=.3,
    random_occupancy=0001)
  if (0 or verbose):
    structure_ideal.show_summary().show_scatterers()
    if (anisotropic_flag):
      uc = structure_ideal.unit_cell()
      for scatterer in structure_ideal.scatterers():
        print "u_iso:", adptbx.u_star_as_u_iso(uc, scatterer.u_star)
    print
  f_obs = abs(structure_ideal.structure_factors(
    d_min=d_min, anomalous_flag=0001, direct=0001).f_calc())
  if (1):
    site(structure_ideal, d_min, f_obs, verbose=verbose)
  if (1):
    if (not anisotropic_flag):
      u_iso(structure_ideal, d_min, f_obs, verbose=verbose)
    else:
      u_star(structure_ideal, d_min, f_obs, verbose=verbose)
  if (1):
    occupancy(structure_ideal, d_min, f_obs, verbose=verbose)
  if (1):
    fp(structure_ideal, d_min, f_obs, verbose=verbose)
  if (1):
    fdp(structure_ideal, d_min, f_obs, verbose=verbose)

def run_call_back(flags, space_group_info):
  for fdp_flag in [0,1]:
    for anisotropic_flag in [0,1]:
      run_one(
        space_group_info=space_group_info,
        fdp_flag=fdp_flag,
        anisotropic_flag=anisotropic_flag,
        verbose=flags.Verbose)

def run():
  debug_utils.parse_options_loop_space_groups(sys.argv[1:], run_call_back)
  print "OK"

if (__name__ == "__main__"):
  run()
