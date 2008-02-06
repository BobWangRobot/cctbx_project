from cctbx.array_family import flex
import math, time
from libtbx import adopt_init_args
from libtbx.test_utils import approx_equal, not_approx_equal
import sys, random
from stdlib import math
from cctbx import xray
from cctbx import adptbx
import mmtbx.restraints
from iotbx import pdb
from cctbx import geometry_restraints
from cctbx.geometry_restraints.lbfgs import lbfgs as cctbx_geometry_restraints_lbfgs
import scitbx.lbfgs
from libtbx.utils import Sorry, user_plus_sys_time
from mmtbx.tls import tools
from cctbx import adp_restraints
from mmtbx import ias
from mmtbx import utils
from mmtbx import model_statistics
from mmtbx.solvent import ordered_solvent
import iotbx.pdb


time_model_show = 0.0

class pdb_structure(object):
  def __init__(self, xray_structure, atom_attributes_list):
    self.xray_structure = xray_structure
    self.atom_attributes_list = atom_attributes_list

  def select(selection):
    self.xray_structure = self.xray_structure.select(selection)
    new_atom_attributes_list = []
    for attr, sel in zip(self.atom_attributes_list, selection):
      if(sel): new_atom_attributes_list.append(attr)
    self.atom_attributes_list = new_atom_attributes_list


class xh_connectivity_table(object):
  # XXX need angle information as well
  def __init__(self, geometry, xray_structure):
    bond_proxies_simple = geometry.geometry.pair_proxies(sites_cart =
      xray_structure.sites_cart()).bond_proxies.simple
    self.table = []
    scatterers = xray_structure.scatterers()
    for proxy in bond_proxies_simple:
      i_seq, j_seq = proxy.i_seqs
      i_x, i_h = None, None
      if(scatterers[i_seq].element_symbol() == "H"):
        i_h = i_seq
        i_x = j_seq
        const_vect = flex.double(scatterers[i_h].site)- \
          flex.double(scatterers[i_x].site)
        self.table.append([i_x, i_h, const_vect, proxy.distance_ideal])
      if(scatterers[j_seq].element_symbol() == "H"):
        i_h = j_seq
        i_x = i_seq
        const_vect = flex.double(scatterers[i_h].site)- \
          flex.double(scatterers[i_x].site)
        self.table.append([i_x, i_h, const_vect, proxy.distance_ideal])

class manager(object):
  def __init__(self, xray_structure,
                     atom_attributes_list,
                     restraints_manager = None,
                     ias_xray_structure = None,
                     refinement_flags = None,
                     ias_manager = None,
                     wilson_b = None,
                     tls_groups = None,
                     anomalous_scatterer_groups = None,
                     log = None):
    self.log = log
    self.restraints_manager = restraints_manager
    self.xray_structure = xray_structure
    self.xray_structure_initial = self.xray_structure.deep_copy_scatterers()
    self.atom_attributes_list = atom_attributes_list
    self.refinement_flags = refinement_flags
    self.wilson_b = wilson_b
    self.tls_groups = tls_groups
    if(anomalous_scatterer_groups is not None and
      len(anomalous_scatterer_groups) == 0):
      anomalous_scatterer_groups = None
    self.anomalous_scatterer_groups = anomalous_scatterer_groups
    # IAS related, need a real cleaning!
    self.ias_manager = ias_manager
    self.ias_xray_structure = ias_xray_structure
    self.use_ias = False
    self.ias_selection = None

  def xh_connectivity_table(self):
    result = None
    if(self.restraints_manager is not None):
      if(self.xray_structure.hd_selection().count(True) > 0):
        result = xh_connectivity_table(
          geometry       = self.restraints_manager,
          xray_structure = self.xray_structure).table
    return result

  def idealize_h(self, xh_bond_distance_deviation_limit):
    from mmtbx.command_line import geometry_minimization
    import scitbx.lbfgs
    lbfgs_termination_params = scitbx.lbfgs.termination_parameters(
      max_iterations = 500)
    geometry_restraints_flags = geometry_restraints.flags.flags(
      bond      = True,
      nonbonded = False,
      angle     = True,
      dihedral  = True,
      chirality = True,
      planarity = True)
    for i in xrange(3):
      sites_cart = self.xray_structure.sites_cart()
      minimized = geometry_minimization.lbfgs(
        sites_cart                  = sites_cart,
        geometry_restraints_manager = self.restraints_manager.geometry,
        geometry_restraints_flags   = geometry_restraints_flags,
        lbfgs_termination_params    = lbfgs_termination_params,
        sites_cart_selection        = self.xray_structure.hd_selection())
      self.xray_structure.set_sites_cart(sites_cart = sites_cart)

  def geometry_minimization(self,
                            max_number_of_iterations = 100,
                            number_of_macro_cycles   = 100):
    raise RuntimeError("Not implemented.")
    if(max_number_of_iterations == 0 or number_of_macro_cycles == 0): return
    sso_start = stereochemistry_statistics(
                          xray_structure         = self.xray_structure,
                          restraints_manager     = self.restraints_manager,
                          use_ias                = self.use_ias,
                          ias_selection          = self.ias_selection,
                          text                   = "start")
    sites_cart = self.xray_structure.sites_cart()
    first_target_value = None
    for macro_cycles in xrange(1,number_of_macro_cycles+1):
        minimized = cctbx_geometry_restraints_lbfgs(
          sites_cart                  = sites_cart,
          geometry_restraints_manager = self.restraints_manager.geometry,
          lbfgs_termination_params    = scitbx.lbfgs.termination_parameters(
                                    max_iterations = max_number_of_iterations))
        if(first_target_value is None):
           first_target_value = minimized.first_target_value
    self.xray_structure = \
                 self.xray_structure.replace_sites_cart(new_sites = sites_cart)
    sso_end = stereochemistry_statistics(
                          xray_structure         = self.xray_structure,
                          restraints_manager     = self.restraints_manager,
                          use_ias                = self.use_ias,
                          ias_selection          = self.ias_selection,
                          text                   = "final")
    assert approx_equal(first_target_value, sso_start.target)
    assert approx_equal(minimized.final_target_value, sso_end.target)
    sso_start.show(out = self.log)
    sso_end.show(out = self.log)

  def extract_ncs_groups(self):
    result = None
    if(self.restraints_manager.ncs_groups is not None):
      result = self.restraints_manager.ncs_groups.extract_ncs_groups(
        sites_cart = self.xray_structure.sites_cart())
    return result

  def deep_copy(self):
    return self.select(selection = flex.bool(
      self.xray_structure.scatterers().size(), True))

  def add_ias(self, fmodel=None, ias_params=None, file_name=None,
                                                             build_only=False):
    if(self.ias_manager is not None):
       self.remove_ias()
       fmodel.update_xray_structure(xray_structure = self.xray_structure,
                                    update_f_calc = True)
    print >> self.log, ">>> Adding IAS.........."
    self.old_refinement_flags = None
    if not build_only: self.use_ias = True
    self.ias_manager = ias.manager(
                    geometry             = self.restraints_manager.geometry,
                    atom_attributes_list = self.atom_attributes_list,
                    xray_structure       = self.xray_structure,
                    fmodel               = fmodel,
                    params               = ias_params,
                    file_name            = file_name,
                    log                  = self.log)
    if(not build_only):
      self.ias_xray_structure = self.ias_manager.ias_xray_structure
      ias_size = self.ias_xray_structure.scatterers().size()
      tail = flex.bool(ias_size, True)
      tail_false = flex.bool(ias_size, False)
      self.ias_selection = flex.bool(
                      self.xray_structure.scatterers().size(),False)
      self.ias_selection.extend(tail)
      self.xray_structure.concatenate_inplace(other = self.ias_xray_structure)
      print >> self.log, "Scattering dictionary for combined xray_structure:"
      self.xray_structure.scattering_type_registry().show()
      self.xray_structure_initial.concatenate_inplace(
                                           other = self.ias_xray_structure)
      if(self.refinement_flags is not None):
         self.old_refinement_flags = self.refinement_flags.deep_copy()
         # define flags
         ssites = flex.bool(self.ias_xray_structure.scatterers().size(), False)
         sadp = flex.bool(self.ias_xray_structure.scatterers().size(), False)
         # XXX set occ refinement ONLY for involved atoms
         # XXX now it refines only occupancies of IAS !!!
         occupancy_flags = []
         ms = self.ias_selection.count(False)
         for i in range(1, self.ias_selection.count(True)+1):
           occupancy_flags.append(flex.size_t([ms+i-1]))
         # set flags
         self.refinement_flags.inflate(
           sites_individual       = ssites,
           occupancies_individual = occupancy_flags,
           adp_individual_iso     = sadp,
           adp_individual_aniso   = sadp)
         # adjust flags
         self.refinement_flags.sites_individual.set_selected(self.ias_selection, False)
         self.refinement_flags.sites_individual.set_selected(~self.ias_selection, True)
         self.refinement_flags.adp_individual_aniso.set_selected(self.ias_selection, False)
         self.refinement_flags.adp_individual_iso.set_selected(self.ias_selection, True)

         #occs = flex.double(self.xray_structure.scatterers().size(), 0.9)
         #self.xray_structure.scatterers().set_occupancies(occs, ~self.ias_selection)
         # D9
         sel = self.xray_structure.scatterers().extract_scattering_types() == "D9"
         self.xray_structure.convert_to_anisotropic(selection = sel)
         self.refinement_flags.adp_individual_aniso.set_selected(sel, True)
         self.refinement_flags.adp_individual_iso.set_selected(sel, False)
    # add to aal:
    i_seq = 0
    for sc in self.ias_xray_structure.scatterers():
      i_seq += 1
      new_atom_name = sc.label.strip()
      if(len(new_atom_name) < 4): new_atom_name = " " + new_atom_name
      while(len(new_atom_name) < 4): new_atom_name = new_atom_name+" "
      new_attr = pdb.atom.attributes(name        = new_atom_name,
                                     resName     = "IAS",
                                     element     = sc.element_symbol(),
                                     is_hetatm   = True,
                                     resSeq      = i_seq)
      self.atom_attributes_list.append(new_attr)


  def remove_ias(self):
    print >> self.log, ">>> Removing IAS..............."
    self.use_ias = False
    if(self.ias_manager is not None):
       self.ias_manager = None
    if(self.old_refinement_flags is not None):
       self.refinement_flags = self.old_refinement_flags.deep_copy()
       self.old_refinement_flags = None
    if(self.ias_selection is not None):
       self.xray_structure.select_inplace(selection = ~self.ias_selection)
       n_non_ias = self.ias_selection.count(False)
       self.ias_selection = None
       self.xray_structure.scattering_type_registry().show()
       self.atom_attributes_list = self.atom_attributes_list[:n_non_ias]

  def show_rigid_bond_test(self, out=None):
    if(out is None): out = sys.stdout
    bond_proxies_simple = \
            self.restraints_manager.geometry.pair_proxies().bond_proxies.simple
    scatterers = self.xray_structure.scatterers()
    unit_cell = self.xray_structure.unit_cell()
    rbt_array = flex.double()
    for proxy in bond_proxies_simple:
        i_seqs = proxy.i_seqs
        i,j = proxy.i_seqs
        atom_i = self.atom_attributes_list[i]
        atom_j = self.atom_attributes_list[j]
        if(atom_i.element.strip() not in ["H","D"] and
                                      atom_j.element.strip() not in ["H","D"]):
           sc_i = scatterers[i]
           sc_j = scatterers[j]
           if(sc_i.flags.use_u_aniso() and sc_j.flags.use_u_aniso()):
              p = adp_restraints.rigid_bond_pair(sc_i.site,
                                                 sc_j.site,
                                                 sc_i.u_star,
                                                 sc_j.u_star,
                                                 unit_cell)
              rbt_value = p.delta_z()*10000.
              rbt_array.append(rbt_value)
              print >> out, "%s %s %10.3f"%(atom_i.name, atom_j.name, rbt_value)
    print >> out, "RBT values (*10000):"
    print >> out, "  mean = %.3f"%flex.mean(rbt_array)
    print >> out, "  max  = %.3f"%flex.max(rbt_array)
    print >> out, "  min  = %.3f"%flex.min(rbt_array)


  def restraints_manager_energies_sites(self,
                                        geometry_flags    = None,
                                        compute_gradients = False,
                                        gradients         = None,
                                        disable_asu_cache = False):
    sites_cart = self.xray_structure.sites_cart()
    if(self.use_ias and self.ias_selection is not None and
       self.ias_selection.count(True) > 0):
      sites_cart = sites_cart.select(~self.ias_selection)
    return self.restraints_manager.energies_sites(
      sites_cart        = sites_cart,
      geometry_flags    = geometry_flags,
      compute_gradients = compute_gradients,
      gradients         = gradients,
      disable_asu_cache = disable_asu_cache)

  def solvent_selection(self):
    labels = self.xray_structure.scatterers().extract_labels()
    water = ordered_solvent.water_ids()
    result = flex.bool()
    get_class = iotbx.pdb.common_residue_names_get_class
    for a in self.atom_attributes_list:
      element = (a.element).strip()
      resName = (a.resName).strip()
      name    = (a.name).strip()
      if((element in water.element_types) and
         (name in water.atom_names) and \
         (get_class(name = resName) == "common_water")):
        result.append(True)
      else: result.append(False)
    return result

  def xray_structure_macromolecule(self):
    sel = self.solvent_selection()
    if(self.use_ias): sel = sel | self.ias_selection
    result = self.xray_structure.select(~sel)
    return result

  def select(self, selection):
    new_atom_attributes_list = []
    for attr, sel in zip(self.atom_attributes_list, selection):
      if(sel): new_atom_attributes_list.append(attr)
    new_refinement_flags = None
    if(self.refinement_flags is not None):
      new_refinement_flags = self.refinement_flags.select(selection)
    new_restraints_manager = None
    if(self.restraints_manager is not None):
      new_restraints_manager = self.restraints_manager.select(
        selection = selection)
      new_restraints_manager.geometry.pair_proxies(sites_cart =
        self.xray_structure.sites_cart().select(selection)) # XXX is it necessary ?
    new = manager(
      restraints_manager         = new_restraints_manager,
      xray_structure             = self.xray_structure.select(selection),
      atom_attributes_list       = new_atom_attributes_list,
      refinement_flags           = new_refinement_flags,
      tls_groups                 = self.tls_groups, # XXX not selected, potential bug
      anomalous_scatterer_groups = self.anomalous_scatterer_groups,
      log                        = self.log)
    new.xray_structure_initial = \
      self.xray_structure_initial.deep_copy_scatterers()
    new.xray_structure.scattering_type_registry()
    return new

  def number_of_ordered_solvent_molecules(self):
    return self.solvent_selection().count(True)

  def show_groups(self, rigid_body = None, tls = None,
                        out = None, text="Information about rigid groups"):
    global time_model_show
    timer = user_plus_sys_time()
    selections = None
    if(rigid_body is not None):
       selections = self.refinement_flags.sites_rigid_body
    if(tls is not None): selections = self.refinement_flags.adp_tls
    if(self.refinement_flags.sites_rigid_body is None and
                                 self.refinement_flags.adp_tls is None): return
    assert selections is not None
    if (out is None): out = sys.stdout
    print >> out
    line_len = len("| "+text+"|")
    fill_len = 80 - line_len-1
    upper_line = "|-"+text+"-"*(fill_len)+"|"
    print >> out, upper_line
    next = "| Total number of atoms = %-6d  Number of rigid groups = %-3d                |"
    natoms_total = self.xray_structure.scatterers().size()
    print >> out, next % (natoms_total, len(selections))
    print >> out, "| group: start point:                        end point:                       |"
    print >> out, "|               x      B  atom   residue <>        x      B  atom   residue   |"
    next = "| %5d: %8.3f %6.2f %5s %4s %4s <> %8.3f %6.2f %5s %4s %4s   |"
    sites = self.xray_structure.sites_cart()
    b_isos = self.xray_structure.extract_u_iso_or_u_equiv() * math.pi**2*8
    n_atoms = 0
    for i_seq, selection in enumerate(selections):
        try:
          i_selection = selection.iselection()
          n_atoms += i_selection.size()
        except:
          i_selection = selection
          n_atoms += i_selection.size()
        start = i_selection[0]
        final = i_selection[i_selection.size()-1]
        first = self.atom_attributes_list[start]
        last  = self.atom_attributes_list[final]
        print >> out, next % (i_seq+1, sites[start][0], b_isos[start],
          first.name, first.resName, first.resSeq, sites[final][0],
          b_isos[final], last.name, last.resName, last.resSeq)
    print >> out, "|"+"-"*77+"|"
    print >> out
    out.flush()
    time_model_show += timer.elapsed()

  def remove_solvent(self):
    result = self.select(selection = ~self.solvent_selection())
    return result

  def show_occupancy_statistics(self, out=None, text=""):
    global time_model_show
    timer = user_plus_sys_time()
    # XXX make this more complete and smart
    if(out is None): out = sys.stdout
    print >> out, "|-"+text+"-"*(80 - len("| "+text+"|") - 1)+"|"
    occ = self.xray_structure.scatterers().extract_occupancies()
    occ_min = flex.min(occ)
    occ_max = flex.max(occ)
    n_zeros = (occ < 0.1).count(True)
    percent_small = n_zeros * 100 / occ.size()
    n_large = (occ > 2.0).count(True)
    if(occ_min < 0.0):
       raise Sorry("There are atoms with negative occupancies. Check input "\
                   "PDB file.")
    if(percent_small > 30.0):
       print >> out, "| *** WARNING: there more than 30 % of atoms with small occupancy (< 0.1) *** |"
    if(n_large > 0):
       print >> out, "| *** WARNING: there are some atoms with large occupancy (> 2.0) ***          |"
    if(abs(occ_max-occ_min) >= 0.01):
       print >> out, "| occupancies: max = %-6.2f min = %-6.2f number of "\
                     "occupancies < 0.1 = %-6d |"%(occ_max,occ_min,n_zeros)
    else:
       print >> out, "| occupancies: max = %-6.2f min = %-6.2f number of "\
                     "occupancies < 0.1 = %-6d |"%(occ_max,occ_min,n_zeros)
    print >> out, "|"+"-"*77+"|"
    out.flush()
    time_model_show += timer.elapsed()

  def write_pdb_file(self, out, selection = None, xray_structure = None):
    utils.write_pdb_file(xray_structure       = self.xray_structure,
                         atom_attributes_list = self.atom_attributes_list,
                         selection            = selection,
                         out                  = out)

  def add_solvent(self, solvent_xray_structure,
                        solvent_selection,
                        atom_name    = "O",
                        residue_name = "HOH",
                        chain_id     = None,
                        refine_occupancies = False,
                        refine_adp = None):
    assert refine_adp is not None
    # XXX print list(solvent_xray_structure.scatterers().extract_scattering_types())
    if(refine_adp == "isotropic"):
      solvent_xray_structure.convert_to_isotropic()
    elif(refine_adp == "anisotropic"):
      solvent_xray_structure.convert_to_anisotropic()
    else: raise RuntimeError
    ms = self.xray_structure.scatterers().size() #
    self.xray_structure = \
      self.xray_structure.concatenate(solvent_xray_structure)
    occupancy_flags = None
    if(refine_occupancies):
      occupancy_flags = []
      for i in range(1, solvent_xray_structure.scatterers().size()+1):
        occupancy_flags.append(flex.size_t([ms+i-1]))
    if(self.refinement_flags.individual_sites):
      ssites = flex.bool(solvent_xray_structure.scatterers().size(), True)
    else: ssites = None
    if(self.refinement_flags.adp_individual_iso):
      sadp_iso = solvent_xray_structure.use_u_iso()
    else: sadp_iso = None
    if(self.refinement_flags.adp_individual_aniso):
      sadp_aniso = solvent_xray_structure.use_u_aniso()
    else: sadp_aniso = None
    self.refinement_flags.inflate(
      sites_individual       = ssites,
      adp_individual_iso     = sadp_iso,
      adp_individual_aniso   = sadp_aniso,
      occupancies_individual = occupancy_flags)
    new_atom_name = atom_name.strip()
    if(len(new_atom_name) < 4): new_atom_name = " " + new_atom_name
    while(len(new_atom_name) < 4): new_atom_name = new_atom_name+" "
    i_seq = 0
    for sc in solvent_xray_structure.scatterers():
        i_seq += 1
        new_attr = pdb.atom.attributes(name        = new_atom_name,
                                       resName     = residue_name,
                                       chainID     = chain_id,
                                       element     = sc.element_symbol(),
                                       is_hetatm   = True,
                                       resSeq      = i_seq)
        self.atom_attributes_list.append(new_attr)
    geometry = self.restraints_manager.geometry
    number_of_new_solvent = solvent_xray_structure.scatterers().size()
    if(geometry.model_indices is None):
       model_indices = None
    else:
       model_indices = flex.size_t(number_of_new_solvent, 0)
    if(geometry.conformer_indices is None):
       conformer_indices = None
    else:
       conformer_indices = flex.size_t(number_of_new_solvent, 0)
    geometry = geometry.new_including_isolated_sites(
           n_additional_sites  = number_of_new_solvent,
           model_indices       = model_indices,
           conformer_indices   = conformer_indices,
           site_symmetry_table = solvent_xray_structure.site_symmetry_table(),
           nonbonded_types     = flex.std_string(number_of_new_solvent, "OH2"))
    self.restraints_manager = mmtbx.restraints.manager(
                         geometry      = geometry,
                         ncs_groups    = self.restraints_manager.ncs_groups,
                         normalization = self.restraints_manager.normalization)
    if (self.restraints_manager.ncs_groups is not None):
      self.restraints_manager.ncs_groups.register_additional_isolated_sites(
        number=number_of_new_solvent)
    self.restraints_manager.geometry.update_plain_pair_sym_table(
                                 sites_frac = self.xray_structure.sites_frac())
    assert len(self.atom_attributes_list) == \
                                        self.xray_structure.scatterers().size()


  def scale_adp(self, scale_max, scale_min):
    b_isos = self.xray_structure.extract_u_iso_or_u_equiv() * math.pi**2*8
    b_isos_mean = flex.mean(b_isos)
    max_b_iso = b_isos_mean * scale_max
    min_b_iso = b_isos_mean / scale_min
    sel_outliers_max = b_isos > max_b_iso
    sel_outliers_min = b_isos < min_b_iso
    b_isos.set_selected(sel_outliers_max, max_b_iso)
    b_isos.set_selected(sel_outliers_min, min_b_iso)
    self.xray_structure.set_b_iso(values = b_isos)

  def geometry_statistics(self):
    sites_cart = self.xray_structure.sites_cart()
    if(self.use_ias): sites_cart = sites_cart.select(~self.ias_selection)
    return model_statistics.geometry(
      sites_cart         = sites_cart,
      restraints_manager = self.restraints_manager)

  def show_geometry_statistics(self, message = "", out = None):
    global time_model_show
    if(out is None): out = self.log
    timer = user_plus_sys_time()
    result = self.geometry_statistics()
    result.show(message = message, out = out)
    time_model_show += timer.elapsed()
    return result

  def adp_statistics(self):
    return model_statistics.adp(model = self)

  def show_adp_statistics(self,
                          prefix         = "",
                          padded         = False,
                          pdb_deposition = False,
                          out            = None):
    global time_model_show
    if(out is None): out = self.log
    timer = user_plus_sys_time()
    result = self.adp_statistics()
    result.show(out = out, prefix = prefix, padded = padded,
      pdb_deposition = pdb_deposition)
    time_model_show += timer.elapsed()
    return result

  def energies_adp(self, iso_restraints, compute_gradients):
    assert self.refinement_flags is not None
    n_aniso = 0
    if(self.refinement_flags.adp_individual_aniso is not None):
      n_aniso = self.refinement_flags.adp_individual_aniso.count(True)
    if(n_aniso == 0):
      energies_adp_iso = self.restraints_manager.energies_adp_iso(
        xray_structure    = self.xray_structure,
        parameters        = iso_restraints,
        use_u_local_only  = iso_restraints.use_u_local_only,
        compute_gradients = compute_gradients)
      target = energies_adp_iso.target
    else:
      energies_adp_aniso = self.restraints_manager.energies_adp_aniso(
        xray_structure    = self.xray_structure,
        compute_gradients = compute_gradients)
      target = energies_adp_aniso.target
    u_iso_gradients = None
    u_aniso_gradients = None
    if(compute_gradients):
      if(n_aniso == 0):
        u_iso_gradients = energies_adp_iso.gradients
      else:
        u_aniso_gradients = energies_adp_aniso.gradients_aniso_star
        u_iso_gradients = energies_adp_aniso.gradients_iso
    class result(object):
      def __init__(self):
        self.target = target
        self.u_iso_gradients = u_iso_gradients
        self.u_aniso_gradients = u_aniso_gradients
    return result()

  def set_refine_individual_sites(self, selection = None):
    self.xray_structure.scatterers().flags_set_grads(state=False)
    if(selection is None):
      selection = self.refinement_flags.sites_individual
    self.xray_structure.scatterers().flags_set_grad_site(
      iselection = selection.iselection())

  def set_refine_individual_adp(self, selection_iso = None,
                                      selection_aniso = None,
                                      h_mode = None):
    self.xray_structure.scatterers().flags_set_grads(state=False)
    if(selection_iso is None):
      selection_iso = self.refinement_flags.adp_individual_iso
      if(selection_iso is not None):
        if(h_mode is not None and h_mode != "individual"):
          selection_iso.set_selected(self.xray_structure.hd_selection(), False)
    if(selection_iso is not None):
      self.xray_structure.scatterers().flags_set_grad_u_iso(
        iselection = selection_iso.iselection())
    if(selection_aniso is None):
      selection_aniso = self.refinement_flags.adp_individual_aniso
      if(selection_aniso is not None):
        if(h_mode is not None and h_mode != "individual"):
          selection_aniso.set_selected(self.xray_structure.hd_selection(),False)
    if(selection_aniso is not None):
      self.xray_structure.scatterers().flags_set_grad_u_aniso(
        iselection = selection_aniso.iselection())
