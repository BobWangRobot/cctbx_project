from __future__ import absolute_import, division, print_function
import sys
from libtbx.utils import Sorry
from cctbx import maptbx
from libtbx import group_args
from scitbx.array_family import flex
from iotbx.map_manager import map_manager
from mmtbx.model import manager as model_manager
from libtbx.utils import null_out
from libtbx.test_utils import approx_equal

class map_model_base(object):

  def write_map(self, file_name = None, log = sys.stdout):
    if not self._map_manager:
      print ("No map to write out", file = log)
    elif not file_name:
      print ("Need file name to write map", file = log)
    else:
      self._map_manager.write_map(file_name = file_name)

  def write_model(self,
     file_name = None,
     log = sys.stdout):
    if not self._model:
      print ("No model to write out", file = log)
    elif not file_name:
      print ("Need file name to write model", file = log)
    else:
      # Write out model

      f = open(file_name, 'w')
      print(self._model.model_as_pdb(), file = f)
      f.close()
      print("Wrote model with %s residues to %s" %(
         self._model.get_hierarchy().overall_counts().n_residues,
         file_name), file = log)

class map_model_manager(map_model_base):

  '''
    Class for shifting origin of map(s) and model to (0, 0, 0) and keeping
    track of the shifts.

    Typical use:
    mam = map_model_manager(
      model = model,
      map_manager = map_manager,
      ncs_object = ncs_object)

    mam.box_around_model(wrapping=False, box_cushion=3)

    shifted_model = mam.model()  # at (0, 0, 0), knows about shifts
    shifted_map_manager = mam.map_manager() # also at (0, 0, 0) knows shifts
    shifted_ncs_object = mam.ncs_object() # also at (0, 0, 0) and knows shifts

    Optional:  apply soft mask to map (requires resolution)
  '''
  def __init__(self,
               model            = None,
               map_manager      = None,  # replaces map_data
               map_manager_1    = None,  # replaces map_data_1
               map_manager_2    = None,  # replaces map_data_2
               map_manager_list = None,  # replaces map_data_list
               ncs_object       = None):

    self._model = model
    self._map_manager = map_manager
    self._map_manager_1 = map_manager_1
    self._map_manager_2 = map_manager_2
    self._map_manager_list = map_manager_list
    self._shift_cart = None
    self._ncs_object = ncs_object
    self._original_origin_grid_units = None
    self._original_origin_cart = None
    self._gridding_first = None
    self._gridding_last = None
    self._solvent_content = None

    # If no map_manager, do not do anything and make sure there is nothing else

    if not map_manager:
      assert not map_manager_1 and not map_manager_2 and not map_manager_list
      assert not ncs_object and not model
      return  # do not do anything

    # CHECKS


    # Make sure that map_manager is either already shifted to (0, 0, 0) or has
    #   origin_shift_grid_unit of (0, 0, 0).
    assert self._map_manager.origin_is_zero() or \
      self._map_manager.origin_shift_grid_units == (0, 0, 0)

    # Normally map_manager unit_cell_crystal_symmetry should match
    #  model original_crystal_symmetry (and also usually model.crystal_symmetry)

    # Make sure we have what is expected: optional model, mm,
    # self._map_manager_1 and self._map_manager_2 or neither,
    #   optional list of self._map_manager_list

    if not self._map_manager_list:
      self._map_manager_list = []

    if(not [self._map_manager_1, self._map_manager_2].count(None) in [0, 2]):
      raise Sorry("None or two half-maps are required.")
    if(not self._map_manager):
      raise Sorry("A map is required.")

    # Make sure all map_managers have same gridding and symmetry
    for m in [self._map_manager_1, self._map_manager_2]+ \
         self._map_manager_list:
      if m:
        assert self._map_manager.is_similar(m)

    # READY

    # Make a match_map_model_ncs and check unit_cell and working crystal symmetry
    #  and shift_cart for model, map, and ncs_object (if present)

    mmmn = match_map_model_ncs()
    mmmn.add_map_manager(self._map_manager)
    if self._model:
      mmmn.add_model(self._model, set_model_log_to_null = False) # keep the log
    if self._ncs_object:
      mmmn.add_ncs_object(self._ncs_object)

    # All ok here if it did not stop

    # Shift origin of model and map_manager to (0, 0, 0) with
    #    mmmn which knows about both
    mmmn.shift_origin(log = null_out())
    self._model = mmmn.model()  # this model knows about shift so far
                             # NOTE: NO SHIFTS ALLOWED COMING IN
    self._map_manager = mmmn.map_manager()  # map_manager also knows about shift
    self._ncs_object = mmmn.ncs_object()  # ncs object also knows about shift
    self._crystal_symmetry = self._map_manager.crystal_symmetry()

    if self._model:
      self._shift_cart = self._model.shift_cart()
      # Make sure model shift manager agrees with map_manager shift
      if self._shift_cart and self._map_manager:
        assert approx_equal(self._shift_cart,
          self._map_manager.shift_cart())

    # Shift origins of all other maps
    for m in [self._map_manager_1, self._map_manager_2]+\
         self._map_manager_list:
      if m:
        m.shift_origin()

    # Make sure all really match:
    for m in [self._map_manager_1, self._map_manager_2]+\
        self._map_manager_list:
      if m:
        assert self._map_manager.is_similar(m)

    # Save origin after origin shift but before any boxing
    #    so they can be accessed easily later

    self._original_origin_grid_units = self._map_manager.origin_shift_grid_units
    self._original_origin_cart = tuple(
       [-x for x in self._map_manager.shift_cart()])

    #  Save gridding of this original map (after shifting, whole thing):
    self._gridding_first = (0, 0, 0)
    self._gridding_last = self._map_manager.map_data().all()

    # Holder for solvent content used in boxing and transferred to box_object
    self._solvent_content = None

  def box_around_model(self,
     wrapping = None,
     box_cushion = 5.):

    '''
       Box all maps around the model, shift origin of maps, model, ncs_object

       wrapping must be specified. Wrapping means map is infinite and repeats
       outside unit cell. Requires a full unit cell in the maps.
    '''
    assert isinstance(self._model, model_manager)
    assert isinstance(wrapping, bool) # must be decided by programmer
    assert box_cushion is not None

    from cctbx.maptbx.box import around_model
    if(self._map_manager_1 is not None):
      tmp_box = around_model(
        map_manager = self._map_manager_1,
        model = self._model.deep_copy(),
        cushion = box_cushion,
        wrapping = wrapping)
      self._map_manager_1 = tmp_box.map_manager()
      tmp_box = around_model(
        map_manager = self._map_manager_2,
        model = self._model.deep_copy(),
        cushion = box_cushion,
        wrapping = wrapping)
      self._map_manager_2 = tmp_box.map_manager()
    if self._map_manager_list:
      new_list = []
      for x in self._map_manager_list:
        tmp_box = around_model(
          map_manager = x,
          model = self._model.deep_copy(),
          cushion = box_cushion,
          wrapping = wrapping)
        new_list.append(tmp_box.map_manager())
      self._map_manager_list = new_list

    # Make box around model
    box = around_model(
      map_manager = self._map_manager,
      model = self._model,
      ncs_object = self._ncs_object,
      cushion = box_cushion,
      wrapping = wrapping)

    box_as_mam = box.as_map_model_manager()

    # New map_manager and model know about cumulative shifts (original
    #   shift to move origin to (0, 0, 0) plus shift from boxing
    self._map_manager = box_as_mam.map_manager()
    self._model = box_as_mam.model()
    self._ncs_object = box_as_mam.ncs_object()

    self._shift_cart = self._model.shift_cart()

    # Update self._crystal_symmetry
    self._crystal_symmetry = self._model.crystal_symmetry()
    assert self._crystal_symmetry.is_similar_symmetry(
      self._map_manager.crystal_symmetry())


  def soft_mask_all_maps_around_edges(self,
      resolution = None,
      soft_mask_radius = None):

    # Apply a soft mask around edges of all maps. Overwrites values in maps

    for mm in self.all_map_managers():
      if not mm: continue
      mm.create_mask_around_edges(
        soft_mask_radius = soft_mask_radius)
      mm.apply_mask()

  def mask_all_maps_around_model(self,
      mask_atoms_atom_radius = None,
      set_outside_to_mean_inside = None,
      soft_mask = None,
      soft_mask_radius = None):
    assert mask_atoms_atom_radius is not None
    assert (not soft_mask) or (soft_mask_radius is not None)
    assert self.model() is not None

    # Apply a mask to all maps. Overwrites values in these maps

    for mm in self.all_map_managers():
      if not mm: continue
      mm.create_mask_around_atoms(
         model = self.model(),
         mask_atoms_atom_radius = mask_atoms_atom_radius)
      if soft_mask:
        mm.soft_mask(soft_mask_radius = soft_mask_radius)
      mm.apply_mask(
         set_outside_to_mean_inside = \
           set_outside_to_mean_inside)

  def original_origin_cart(self):
    assert self._original_origin_cart is not None
    return self._original_origin_cart

  def original_origin_grid_units(self):
    assert self._original_origin_grid_units is not None
    return self._original_origin_grid_units

  def map_data(self):
    return self.map_manager().map_data()

  def map_data_1(self):
    if self.map_manager_1():
      return self.map_manager_1().map_data()

  def map_data_2(self):
    if self.map_manager_2():
      return self.map_manager_2().map_data()

  def all_map_managers(self):
    all_map_managers_list = []
    for x in [self.map_manager()]+[self.map_manager_1()]+\
        [self.map_manager_2()]+ self.map_manager_list():
      if x: all_map_managers_list.append(x)
    return all_map_managers_list

  def map_data_list(self):
    map_data_list = []
    for mm in self.map_manager_list():
      map_data_list.append(mm.map_data())
    return map_data_list

  def map_manager(self):
     return self._map_manager

  def map_manager_1(self):
     return self._map_manager_1

  def map_manager_2(self):
     return self._map_manager_2

  def map_manager_list(self):
     if self._map_manager_list:
       return self._map_manager_list
     else:
       return []

  def model(self): return self._model

  def ncs_object(self): return self._ncs_object

  def crystal_symmetry(self): return self._crystal_symmetry

  def xray_structure(self):
    if(self.model() is not None):
      return self.model().get_xray_structure()
    else:
      return None

  def hierarchy(self): return self._model.get_hierarchy()

  def set_gridding_first(self, gridding_first):
    self._gridding_first = tuple(gridding_first)

  def set_gridding_last(self, gridding_last):
    self._gridding_last = tuple(gridding_last)

  def set_solvent_content(self, solvent_content):
    self._solvent_content = solvent_content

  def get_counts_and_histograms(self):
    self._counts = get_map_counts(
      map_data         = self.map_data(),
      crystal_symmetry = self.crystal_symmetry())
    self._map_histograms = get_map_histograms(
        data    = self.map_data(),
        n_slots = 20,
        data_1  = self.map_data_1(),
        data_2  = self.map_data_2())

  def counts(self):
    if not hasattr(self, '_counts'):
      self.get_counts_and_histograms()
    return self._counts

  def histograms(self):
    if not hasattr(self, '_map_histograms'):
      self.get_counts_and_histograms()
    return self._map_histograms

  def generate_map(self,
      output_map_file_name = None,
      map_coeffs = None,
      high_resolution = 3,
      gridding = None,
      origin_shift_grid_units = None,
      low_resolution_fourier_noise_fraction = 0,
      high_resolution_fourier_noise_fraction = 0,
      low_resolution_real_space_noise_fraction = 0,
      high_resolution_real_space_noise_fraction = 0,
      low_resolution_noise_cutoff = None,
      model = None,
      output_map_coeffs_file_name = None,
      scattering_table = 'electron',
      file_name = None,
      n_residues = 10,
      start_res = None,
      b_iso = 30,
      box_buffer = 5,
      space_group_number = 1,
      output_model_file_name = None,
      shake = None,
      random_seed = None,
      log = sys.stdout):

    '''
      Generate a map using generate_model and generate_map_coefficients

      Summary:
      --------

      Calculate a map and optionally add noise to it.  Supply map
      coefficients (miller_array object) and types of noise to add,
      along with optional gridding (nx, ny, nz), and origin_shift_grid_units.
      Optionally create map coefficients from a model and optionally
      generate a model.

      Unique aspect of this noise generation is that it can be specified
      whether the noise is local in real space (every point in a map
      gets a random value before Fourier filtering), or local in Fourier
      space (every Fourier coefficient gets a complex random offset).
      Also the relative contribution of each type of noise vs resolution
      can be controlled.

      Parameters:
      -----------

      Used in generate_map:
      -----------------------

      output_map_file_name (string, None):  Output map file (MRC/CCP4 format)
      map_coeffs (miller.array object, None) : map coefficients
      high_resolution (float, 3):      high_resolution limit (A)
      gridding (tuple (nx, ny, nz), None):  Gridding of map (optional)
      origin_shift_grid_units (tuple (ix, iy, iz), None):  Move location of
          origin of resulting map to (ix, iy, iz) before writing out
      low_resolution_fourier_noise_fraction (float, 0): Low-res Fourier noise
      high_resolution_fourier_noise_fraction (float, 0): High-res Fourier noise
      low_resolution_real_space_noise_fraction(float, 0): Low-res
          real-space noise
      high_resolution_real_space_noise_fraction (float, 0): High-res
          real-space noise
      low_resolution_noise_cutoff (float, None):  Low resolution where noise
          starts to be added


      Pass-through to generate_map_coefficients (if map_coeffs is None):
      -----------------------
      model (model.manager object, None):    model to use
      output_map_coeffs_file_name (string, None): output model file name
      high_resolution (float, 3):   High-resolution limit for map coeffs (A)
      scattering_table (choice, 'electron'): choice of scattering table
           All choices: wk1995 it1992 n_gaussian neutron electron

      Pass-through to generate_model (used if map_coeffs and model are None):
      -------------------------------

      file_name (string, None):  File containing model (PDB, CIF format)
      n_residues (int, 10):      Number of residues to include
      start_res (int, None):     Starting residue number
      b_iso (float, 30):         B-value (ADP) to use for all atoms
      box_buffer (float, 5):     Buffer (A) around model
      space_group_number (int, 1):  Space group to use
      output_model_file_name (string, None):  File for output model
      shake (float, None):       RMS variation to add (A) in shake
      random_seed (int, None):    Random seed for shake

    '''


    print("\nGenerating new map data\n", file = log)
    if self._map_manager:
      print("NOTE: replacing existing map data\n", file = log)
    if self._model and  file_name:
      print("NOTE: using existing model to generate map data\n", file = log)
      model = self._model
    else:
      model = None

    from iotbx.create_models_or_maps import generate_model, \
       generate_map_coefficients
    from iotbx.create_models_or_maps import generate_map as generate_map_data

    if not model and not map_coeffs:
      model = generate_model(
        file_name = file_name,
        n_residues = n_residues,
        start_res = start_res,
        b_iso = b_iso,
        box_buffer = box_buffer,
        space_group_number = space_group_number,
        output_model_file_name = output_model_file_name,
        shake = shake,
        random_seed = random_seed,
        log = log)

    if not map_coeffs:
      map_coeffs = generate_map_coefficients(model = model,
        high_resolution = high_resolution,
        output_map_coeffs_file_name = output_map_coeffs_file_name,
        scattering_table = scattering_table,
        log = log)

    mm = generate_map_data(
      output_map_file_name = output_map_file_name,
      map_coeffs = map_coeffs,
      high_resolution = high_resolution,
      gridding = gridding,
      origin_shift_grid_units = origin_shift_grid_units,
      low_resolution_fourier_noise_fraction = \
        low_resolution_fourier_noise_fraction,
      high_resolution_fourier_noise_fraction = \
        high_resolution_fourier_noise_fraction,
      low_resolution_real_space_noise_fraction = \
        low_resolution_real_space_noise_fraction,
      high_resolution_real_space_noise_fraction = \
        high_resolution_real_space_noise_fraction,
      low_resolution_noise_cutoff = low_resolution_noise_cutoff,
      log = log)

    mm.show_summary()
    self._map_manager = mm
    self._model = model

  def deep_copy(self):
    new_mmm = map_model_manager()
    new_mmm._model = None
    new_mmm._map_manager = None
    new_mmm._map_manager_1 = None 
    new_mmm._map_manager_2 = None 
    new_mmm._map_manager_list = None
    new_mmm._shift_cart = None
    new_mmm._ncs_object = None

    from copy import deepcopy
    new_mmm._original_origin_grid_units=deepcopy(
        self._original_origin_grid_units)
    new_mmm._original_origin_cart=deepcopy(self._original_origin_cart)
    new_mmm._gridding_first=deepcopy(self._gridding_first)
    new_mmm._gridding_last=deepcopy(self._gridding_last)
    new_mmm._solvent_content=deepcopy(self._solvent_content)
    if self._model:
       new_mmm._model=self._model.deep_copy()
    if self._map_manager:
       new_mmm._map_manager=self._map_manager.deep_copy()
    if self._ncs_object:
       new_mmm._ncs_object=self._ncs_object.deep_copy()
    return new_mmm

  def as_map_model_manager(self):
    '''
      Return this object (allows using .as_map_model_manager() on both
      map_model_manager objects and others including box.around_model() etc.
    '''
    return self

  def as_match_map_model_ncs(self):
    '''
      Return this object as a match_map_model_ncs
    '''
    from iotbx.map_model_manager import match_map_model_ncs
    mmmn = match_map_model_ncs()
    if self.map_manager():
      mmmn.add_map_manager(self.map_manager())
    if self.model():
      mmmn.add_model(self.model())
    if self.ncs_object():
      mmmn.add_ncs_object(self.ncs_object())
    return mmmn

  def as_box_object(self,
        original_map_data = None,
        solvent_content = None):
    '''
      Create a box_object for backwards compatibility with methods that used
       extract_box_around_model_and_map
    '''

    if solvent_content:
      self.set_solvent_content(solvent_content)

    if self.model():
       xray_structure_box = self.model().get_xray_structure()
       hierarchy = self.model().get_hierarchy()
    else:
       xray_structure_box = None
       hierarchy = None

    output_box = box_object(
      shift_cart = tuple([-x for x in self.map_manager().origin_shift_cart()]),
      xray_structure_box = xray_structure_box,
      hierarchy = hierarchy,
      ncs_object = self.ncs_object(),
      map_box = self.map_manager().map_data(),
      map_data = original_map_data,
      map_box_half_map_list = None,
      box_crystal_symmetry = self.map_manager().crystal_symmetry(),
      pdb_outside_box_msg = "",
      gridding_first = self._gridding_first,
      gridding_last = self._gridding_last,
      solvent_content = self._solvent_content,
      origin_shift_grid_units = [
         -x for x in self.map_manager().origin_shift_grid_units],
      )
    return output_box

def get_map_histograms(data, n_slots = 20, data_1 = None, data_2 = None):
  h0, h1, h2 = None, None, None
  data_min = None
  hmhcc = None
  if(data_1 is None):
    h0 = flex.histogram(data = data.as_1d(), n_slots = n_slots)
  else:
    data_min = min(flex.min(data_1), flex.min(data_2))
    data_max = max(flex.max(data_1), flex.max(data_2))
    h0 = flex.histogram(data = data.as_1d(), n_slots = n_slots)
    h1 = flex.histogram(data = data_1.as_1d(), data_min = data_min,
      data_max = data_max, n_slots = n_slots)
    h2 = flex.histogram(data = data_2.as_1d(), data_min = data_min,
      data_max = data_max, n_slots = n_slots)
    hmhcc = flex.linear_correlation(
      x = h1.slots().as_double(),
      y = h2.slots().as_double()).coefficient()
  return group_args(h_map = h0, h_half_map_1 = h1, h_half_map_2 = h2,
    _data_min = data_min, half_map_histogram_cc = hmhcc)

def get_map_counts(map_data, crystal_symmetry = None):
  a = map_data.accessor()
  map_counts = group_args(
    origin       = a.origin(),
    last         = a.last(),
    focus        = a.focus(),
    all          = a.all(),
    min_max_mean = map_data.as_1d().min_max_mean().as_tuple(),
    d_min_corner = maptbx.d_min_corner(map_data = map_data,
      unit_cell = crystal_symmetry.unit_cell()))
  return map_counts

def add_tuples(t1, t2):
  new_list = []
  for a, b in zip(t1, t2):
    new_list.append(a+b)
  return tuple(new_list)

class match_map_model_ncs(map_model_base):

  '''
   match_map_model_ncs

   Use: Container to hold maps, models, ncs objects, with high-level tools to
   generate them, manipulate them, and keep track of origin shifts and
   cell dimensions of boxed maps.

   Main tools:
     Read and write maps and models and NCS objects in their original
       coordinate systems
     Box maps and models

   See notes in iotbx.map_manager for information about MRC/CCP4 maps and
     how they represent part or all of a "unit cell".


   Normal usage:

     Initialize empty, then read in or add a group of model.manager,
     map_manager, and ncs objects

     Optional: read in the models, maps, ncs objects

     Optional: box the maps, models, ncs objects and save boxed versions

     Shift origin to (0, 0, 0) and save position of this (0, 0, 0) point in the
        original coordinate system so that everything can be written out
        superimposed on the original locations. This is origin_shift_grid_units
        in grid units

     Can add modified maps/models later if they have the same value of
        origin_shift_grid_units and crystal_symmetry() matches existing
        crystal_symmetry() or unit_cell_crystal_symmetry()
        (dimensions of box of data that is present or dimensions of
        full unit cell)

     Implemented:  Only one map, model at present

     NOTE: modifies the model, map_manager, and ncs objects. Call with
     deep_copy() of these if originals need to be preserved.

     Input models, maps, and ncs_object must all match in crystal_symmetry,
     original (unit_cell) crystal_symmetry, and shift_cart for maps)

  '''

  def __init__(self, ):
    self._map_manager = None
    self._model = None
    self._ncs_object = None

  def deep_copy(self):
    from copy import deepcopy
    new_mmmn = match_map_model_ncs()
    if self._model:
      new_mmmn.add_model(self._model.deep_copy())
    if self._map_manager:
      new_mmmn.add_map_manager(self._map_manager.deep_copy())
    if self._ncs_object:
      new_mmmn.add_ncs_object(self._ncs_object.deep_copy())
    return new_mmmn

  def show_summary(self, log = sys.stdout):
    print ("Summary of maps and models", file = log)
    if self._map_manager:
      print("Map summary:", file = log)
      self._map_manager.show_summary(out = log)
    if self._model:
      print("Model summary:", file = log)
      print("Residues: %s" %(
       self._model.get_hierarchy().overall_counts().n_residues), file = log)

    if self._ncs_object:
      print("NCS summary:", file = log)
      print("Operators: %s" %(
       self._ncs_object.max_operators()), file = log)

  def crystal_symmetry(self):
    # Return crystal symmetry of first map, or if not present, of first model
    if self._map_manager:
      return self._map_manager.crystal_symmetry()
    elif self._model:
      return self._model.crystal_symmetry()
    else:
      return None

  def unit_cell_crystal_symmetry(self):
    # Return unit_cell crystal symmetry of first map
    if self._map_manager:
      return self._map_manager.unit_cell_crystal_symmetry()
    else:
      return None

  def map_manager(self):
    return self._map_manager

  def model(self):
    return self._model

  def ncs_object(self):
    return self._ncs_object


  def add_map_manager(self, map_manager = None, log = sys.stdout):
    # Add a map and make sure its symmetry is similar to others
    self._map_manager = map_manager
    if self.model():
      self.check_model_and_set_to_match_map()

  def check_model_and_set_to_match_map(self):
    # Map, model and ncs_object all must have same symmetry and shifts at end

    if self.map_manager() and self.model():
      # Must be compatible...then set model symmetry if not set
      ok=self.map_manager().is_compatible_model(self.model(),
        require_similar=False)
      if ok:
        self.map_manager().set_model_symmetries_and_shift_cart_to_match_map(
          self.model())  # modifies self.model() in place
      else:
         raise AssertionError(self.map_manager().warning_message())

    if self.map_manager() and self.ncs_object():
      # Must be similar...
      if not self.map_manager().is_similar_ncs_object(self.ncs_object()):
        raise AssertionError(self.map_manager().warning_message())

  def add_model(self, model = None, set_model_log_to_null = True,
     log = sys.stdout):
    # Add a model and make sure its symmetry is similar to others
    # Check that model original crystal_symmetry matches full
    #    crystal_symmetry of map
    if set_model_log_to_null:
      model.set_log(null_out())
    self._model = model
    if self.map_manager():
      self.check_model_and_set_to_match_map()

  def add_ncs_object(self, ncs_object = None, log = sys.stdout):
    # Add an NCS object
    self._ncs_object = ncs_object
    # Check to make sure its shift_cart matches

  def read_map(self, file_name = None, log = sys.stdout):
    # Read in a map and make sure its symmetry is similar to others
    mm = map_manager(file_name)
    self.add_map_manager(mm, log = log)

  def read_model(self, file_name = None, log = sys.stdout):
    print("Reading model from %s " %(file_name), file = log)
    from iotbx.pdb import input
    inp = input(file_name = file_name)
    from mmtbx.model import manager as model_manager
    model = model_manager(model_input = inp)
    self.add_model(model, log = log)


  def read_ncs_file(self, file_name = None, log = sys.stdout):
    # Read in an NCS file and make sure its symmetry is similar to others
    from mmtbx.ncs.ncs import ncs
    ncs_object = ncs()
    ncs_object.read_ncs(file_name = file_name, log = log)
    if ncs_object.max_operators()<2:
       self.ncs_object.set_unit_ncs()
    self.add_ncs_object(ncs_object)

  def set_original_origin_and_gridding(self,
      original_origin = None,
      gridding = None):
    '''
     Use map_manager to reset (redefine) the original origin and gridding
     of the map.
     You can supply just the original origin in grid units, or just the
     gridding of the full unit_cell map, or both.

     Update shift_cart for model and ncs object if present.

    '''

    assert self._map_manager is not None

    self._map_manager.set_original_origin_and_gridding(
         original_origin = original_origin,
         gridding = gridding)

    # Get the current origin shift based on this new original origin
    shift_cart = self._map_manager.shift_cart()
    if self._model:
      if self._model.shift_cart() is None:
        self._model.set_unit_cell_crystal_symmetry_and_shift_cart(
          unit_cell_crystal_symmetry = \
           self._map_manager.unit_cell_crystal_symmetry())
      self._model.set_shift_cart(shift_cart)
    if self._ncs_object:
      self._ncs_object.set_shift_cart(shift_cart)

  def shift_origin(self, desired_origin = (0, 0, 0), log = sys.stdout):
    # shift the origin of all maps/models to desired_origin (usually (0, 0, 0))
    if not self._map_manager:
      print ("No information about origin available", file = log)
      return
    if self._map_manager.map_data().origin() == desired_origin:
      print("Origin is already at %s, no shifts will be applied" %(
       str(desired_origin)), file = log)

    # Figure out shift of model if incoming map and model already had a shift

    if self._ncs_object or self._model:

      # Figure out shift for model and make sure model and map agree
      shift_info = self._map_manager.get_shift_info(
         desired_origin = desired_origin)

      current_shift_cart = self._map_manager.grid_units_to_cart(
       tuple([-x for x in shift_info.current_origin_shift_grid_units]))
      expected_model_shift_cart = current_shift_cart

      shift_to_apply_cart = self._map_manager.grid_units_to_cart(
        shift_info.shift_to_apply)
      new_shift_cart = self._map_manager.grid_units_to_cart(
        tuple([-x for x in shift_info.new_origin_shift_grid_units]))
      new_full_shift_cart = new_shift_cart
      # shift_to_apply_cart is coordinate shift we are going to apply
      #  new_shift_cart is how to get to new location from original
      #   current_shift_cart is how to get to current location from original
      assert approx_equal(shift_to_apply_cart, [(a-b) for a, b in zip(
        new_shift_cart, current_shift_cart)])

      # Get shifts already applied to  model and ncs_object
      #    and check that they match map

      if self._model:
        existing_shift_cart = self._model.shift_cart()
        if existing_shift_cart is not None:
          assert approx_equal(existing_shift_cart, expected_model_shift_cart)

      if self._ncs_object:
        ncs_shift_cart = self._ncs_object.shift_cart()
        assert approx_equal(ncs_shift_cart, expected_model_shift_cart)

      if self._map_manager.origin_is_zero() and \
         expected_model_shift_cart == (0, 0, 0):
        pass # Need to set model shift_cart below

    # Apply shift to model, map and ncs object

    # Shift origin of map_manager
    self._map_manager.shift_origin(desired_origin = desired_origin)

    # Shift origin of model  Note this sets model shift_cart
    if self._model:
      self._model = self.shift_model_to_match_working_map(
        coordinate_shift = shift_to_apply_cart,
        new_shift_cart = new_full_shift_cart,
        model = self._model, log = log)
    if self._ncs_object:
      self._ncs_object = self.shift_ncs_to_match_working_map(
        coordinate_shift = shift_to_apply_cart,
        new_shift_cart = new_full_shift_cart,
        ncs_object = self._ncs_object, log = log)

  def shift_ncs_to_match_working_map(self, ncs_object = None, reverse = False,
    coordinate_shift = None,
    new_shift_cart = None,
    log = sys.stdout):
    # Shift an ncs object to match the working map (based
    #    on self._map_manager.origin_shift_grid_units)
    if coordinate_shift is None:
      coordinate_shift = self.get_coordinate_shift(reverse = reverse)

    ncs_object = ncs_object.coordinate_offset(coordinate_shift)
    return ncs_object

  def shift_ncs_to_match_original_map(self, ncs_object = None, log = sys.stdout):
    return self.shift_ncs_to_match_working_map(ncs_object = ncs_object,
      reverse = True, log = log)

  def get_coordinate_shift(self, reverse = False):
    if reverse: # Get origin shift in grid units  ==  position of original origin
                #  on the current grid
      origin_shift = self._map_manager.origin_shift_grid_units
    else:  # go backwards
      a = self._map_manager.origin_shift_grid_units
      origin_shift = [-a[0], -a[1], -a[2]]

    coordinate_shift = []
    for shift_grid_units, spacing in zip(
       origin_shift, self._map_manager.pixel_sizes()):
      coordinate_shift.append(shift_grid_units*spacing)
    return coordinate_shift

  def shift_model_to_match_working_map(self, model = None, reverse = False,
     coordinate_shift = None,
     new_shift_cart = None,
     log = sys.stdout):

    '''
    Shift a model based on the coordinate shift for the working map.

    Optionally specify the shift to apply (coordinate shift) and the
    new value of the shift recorded in the model (new_shift_cart)
    '''

    if coordinate_shift is None:
      coordinate_shift = self.get_coordinate_shift(
       reverse = reverse)
    if new_shift_cart is None:
      new_shift_cart = coordinate_shift

    model.shift_model_and_set_crystal_symmetry(shift_cart = coordinate_shift,
      crystal_symmetry = model.crystal_symmetry())  # keep crystal_symmetry

    # Allow specifying the final shift_cart:
    if tuple(new_shift_cart) !=  tuple(coordinate_shift):
      model.set_shift_cart(new_shift_cart)

    return model

  def shift_model_to_match_original_map(self, model = None, log = sys.stdout):
    # Shift a model object to match the original map (based
    #    on -self._map_manager.origin_shift_grid_units)
    return self.shift_model_to_match_working_map(model = model, reverse = True,
      log = log)

  def as_map_model_manager(self):

    '''
      Return map_model_manager object with contents of this class 
      (not a deepcopy)

    '''
    from iotbx.map_model_manager import map_model_manager
    mam = map_model_manager(
        map_manager = self.map_manager(),
        model = self.model(),
        ncs_object = self.ncs_object(),
        )
    # Keep track of the gridding and solvent_content (if used) in this boxing.
    return mam
