from cctbx import miller
from cctbx import crystal
from cctbx.array_family import flex

class index_fobs_sigma_line:

  def __init__(self, raw_line):
    self.is_complete = 00000
    flds = raw_line.replace("="," ").split()
    if (len(flds) != 8): return
    if (flds[0].lower() not in ("inde", "index")): return
    if (flds[4].lower() != "fobs"): return
    if (flds[6].lower() != "sigma"): return
    try: self.index = tuple([int(i) for i in flds[1:4]])
    except: return
    try: self.fobs = float(flds[5])
    except: return
    try: self.sigma = float(flds[7])
    except: return
    self.is_complete = 0001

class reader:

  def __init__(self, file_name=None, file_object=None, max_header_lines=30):
    assert [file_name, file_object].count(None) == 1
    if (file_object is None):
      file_object = open(file_name)
    self._indices = flex.miller_index()
    self._data = flex.double()
    self._sigmas = flex.double()
    have_data = 00000
    self.n_lines = 0
    for raw_line in file_object:
      self.n_lines += 1
      ifs = index_fobs_sigma_line(raw_line)
      if (not ifs.is_complete):
        if (raw_line.strip().lower() == "end"):
          break
        if (self.n_lines == max_header_lines or have_data):
          raise RuntimeError, "Unkown file format."
      else:
        self._indices.append(ifs.index)
        self._data.append(ifs.fobs)
        self._sigmas.append(ifs.sigma)
        have_data = 0001

  def indices(self):
    return self._indices

  def data(self):
    return self._data

  def sigmas(self):
    return self._sigmas

  def as_miller_arrays(self, crystal_symmetry=None, force_symmetry=00000,
                             info_prefix=""):
    if (crystal_symmetry is None):
      crystal_symmetry = crystal.symmetry()
    miller_set = miller.set(
      crystal_symmetry=crystal_symmetry,
      indices=self.indices()).auto_anomalous()
    return [miller.array(
      miller_set=miller_set,
      data=self.data(),
      sigmas=self.sigmas())
      .set_info(info_prefix+"fobs,sigma")
      .set_observation_type_xray_amplitude()]
