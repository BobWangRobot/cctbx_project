#include <mmtbx/masks/atom_mask.h>
#include <mmtbx/masks/grid_symop.h>
#include <cctbx/maptbx/structure_factors.h>
#include <cctbx/maptbx/gridding.h>
#include <cctbx/sgtbx/direct_space_asu/proto/small_vec_math.h>
#include <scitbx/fftpack/real_to_complex_3d.h>
#include <scitbx/array_family/flex_types.h>
#include <scitbx/array_family/tiny_types.h>
#include <boost/date_time/posix_time/posix_time.hpp>
#include <numeric>
#include <sstream>

#if defined(_MSC_VER)
#undef max
#undef min
#endif

namespace mmtbx { namespace masks {

  using namespace cctbx::sgtbx::asu;
  typedef double f_t;

  namespace {

    inline int ifloor(f_t x)
    {
      return scitbx::math::float_int_conversions<f_t, int>::ifloor(x);
    }

    inline int iceil(f_t x)
    {
      return scitbx::math::float_int_conversions<f_t, int>::iceil(x);
    }

    inline scitbx::vec3<int> iceil(const scitbx::vec3<double> &v)
    {
      return scitbx::vec3<int> (
          scitbx::math::float_int_conversions<double, int>::iceil(v[0]),
          scitbx::math::float_int_conversions<double, int>::iceil(v[1]),
          scitbx::math::float_int_conversions<double, int>::iceil(v[2])
          );
    }

    inline scitbx::vec3<int> ifloor(const scitbx::vec3<double> &v)
    {
      return scitbx::vec3<int> (
          scitbx::math::float_int_conversions<double, int>::ifloor(v[0]),
          scitbx::math::float_int_conversions<double, int>::ifloor(v[1]),
          scitbx::math::float_int_conversions<double, int>::ifloor(v[2])
          );
    }

  }


  inline void translate_into_cell(scitbx::int3 &num, const scitbx::int3 &den)
  {
    for(register unsigned char i=0; i<3; ++i)
    {
      register int tn = num[i];
      register const int td = den[i];
      tn %= td;
      if( tn < 0 )
        tn += td;
      num[i] = tn;
    }
  }

  // seems to be of the same speed as the above
  inline void translate_into_cell_2(scitbx::int3 &num, const scitbx::int3 &den)
  {
    for(unsigned char i=0; i<3; ++i)
    {
      register int tn = num[i];
      register const int td = den[i];
      while( tn<0 )
        tn += td;
      while( tn >= td )
        tn -= td;
      num[i] = tn;
    }
  }


  unsigned short site_symmetry_order(
    const std::vector<cctbx::sgtbx::grid_symop> &symops,
    const scitbx::int3 &num, const scitbx::int3 &den )
  {
    unsigned short nops = 0;
    // num must be  inside cell
    for(size_t i=0; i<symops.size(); ++i)
    {
      scitbx::int3 sv = symops[i].apply_to( num );
      translate_into_cell(sv, den);
      if( scitbx::eq_all(sv , num) )
        ++nops;
    }
    MMTBX_ASSERT( nops>0U );
    return nops;
  }


  void atom_mask::mask_asu()
  {
    unsigned short order = group.order_z();
    MMTBX_ASSERT( order>0 );
    const af::ref<data_type, grid_t > data_ref = data.ref();
    const scitbx::int3 n = this->grid_size();
    MMTBX_ASSERT( n[0]>0 && n[1]>0 && n[2] >0 );
    // below is to make sure that direct_space_asu::is_inside integer
    // arithmetics will not overflow
    if( static_cast<double>(n[0])*static_cast<double>(n[1])*n[2]
      >= static_cast<double>(std::numeric_limits<long>::max())/(4*8) )
    {
      std::stringstream str;
      str << "the mask grid size: " << n[0] << " * " << n[1] << " * " << n[2]
        << " = " << long(n[0])*long(n[1])*long(n[2]) << " is too big\n"
        << " maximum allowed size is "
        << std::numeric_limits<long>::max() / (4*8)
        << " 64 bit OS and software might be required for such a big mask.";
      throw mmtbx::error(str.str());
    }
    // prepare grid adapted integer symmetry operators
    std::vector<cctbx::sgtbx::grid_symop> symops;
    for(size_t i=0; i<order; ++i)
    {
      cctbx::sgtbx::grid_symop grsym( group(i), n );
      symops.push_back(grsym);
    }
    MMTBX_ASSERT( symops.size() == order );
    scitbx::int3 imn, imx;
    this->get_asu_boundaries(imn, imx); // [imn, imx)

    scitbx::int3 emn, emx; //  enclosed box boundaries [emn, emx]
    const bool has_enclosed_box = asu.enclosed_box_corners(emn, emx, n);
    this->debug_has_enclosed_box = has_enclosed_box;
    if( has_enclosed_box )
    {
      MMTBX_ASSERT( scitbx::gt_all( emn, imn ) );
      MMTBX_ASSERT( scitbx::lt_all( emx, imx ) );
    }

    cctbx::sgtbx::asu::direct_space_asu opt_asu = this->asu;
    opt_asu.optimize_for_grid(n);
    scitbx::int3 smn, smx;
    this->get_expanded_asu_boundaries(smn,smx); // [smn, smx)
    data_type *d_ptr = data_ref.begin();
    for(register long i=smn[0]; i<smx[0]; ++i)
    {
      for(register long j=smn[1]; j<smx[1]; ++j)
      {
        for(register long k=smn[2]; k<smx[2]; ++k, ++d_ptr)
        {
          // set points wthin shrink_truncation_radius around asu to
          // an arbitrary unique positive value
          data_type &dr = *d_ptr;
          if( dr == 0 )
            dr = mark;
          const scitbx::int3 pos(i,j,k);
          if( !(scitbx::ge_all(pos, imn) && scitbx::lt_all(pos, imx)) )
            continue;
          register unsigned short nops = 0;
          if( has_enclosed_box )
          {
            if( scitbx::ge_all(pos, emn) && scitbx::le_all(pos, emx) )
              nops = order;
          }
          if( nops==0 )
          {
            const short ww = opt_asu.where_is(pos);
            if( ww == 1 ) // inside NOT on the face
              nops = order;
            else if( ww==-1 ) // inside on the face
            {
              scitbx::int3 pos_in_cell(pos);
              translate_into_cell(pos_in_cell, n);
              nops = site_symmetry_order(symops, pos_in_cell, n);
              MMTBX_ASSERT( nops>0 );
              MMTBX_ASSERT( order%nops == 0);
              nops = order / nops;
            }
          }
          if( nops!=0 )
          {
              MMTBX_ASSERT( dr==0 || nops == dr
                  || dr==mark );
              dr = nops;
          }
        }
      }
    }
  }


  void atom_mask::compute(
    const coord_array_t & sites_frac,
    const double_array_t & atom_radii)
  {
    boost::posix_time::ptime
      tb = boost::posix_time::microsec_clock::local_time(), te;
    boost::posix_time::time_duration tdif;
    af::const_ref<data_type, grid_t > data_cref = data.const_ref();
    af::ref<data_type, grid_t > data_ref = data.ref();
    std::fill(data_ref.begin(), data_ref.end(), 0);
    this->atoms_to_asu(sites_frac, atom_radii);
    te = boost::posix_time::microsec_clock::local_time();
    tdif = te - tb;
    debug_atoms_to_asu_time = tdif.total_milliseconds();
    tb = boost::posix_time::microsec_clock::local_time();
    // masking is the slowest part of this routine, for not optimized asus
    this->mask_asu();
    // test asu volume
    // TODO: optimization? move this test in mask_asu
    // could save a few millisec
    size_t nn = 0;
    for(af::const_ref<data_type, grid_t >::const_iterator
      msk_it=data_cref.begin(); msk_it!=data_cref.end(); ++msk_it)
    {
      if( *msk_it != -mark && *msk_it != mark )
      {
        MMTBX_ASSERT( *msk_it >= 0 );
        nn += static_cast<size_t>( *msk_it );
      }
    }
    MMTBX_ASSERT( nn > 0 );
    if( nn != this->grid_size_1d() ) // volume(asu)*group_order != volume(cell)
    {
      std::ostringstream str;
      str << "volume(asymmetric unit)*group_order != volume(unit cell).\n"
          << "Maybe because the mask grid size: " << this->grid_size()
          << " is incompatible with\n"
             "the space group symmetry";
      throw mmtbx::error( str.str() );
    }
    te = boost::posix_time::microsec_clock::local_time();
    tdif = te - tb;
    debug_mask_asu_time = tdif.total_milliseconds();
    tb = boost::posix_time::microsec_clock::local_time();
    this->compute_accessible_surface(this->asu_atoms);
    te = boost::posix_time::microsec_clock::local_time();
    tdif = te - tb;
    debug_accessible_time = tdif.total_milliseconds();
    tb = boost::posix_time::microsec_clock::local_time();
    this->compute_contact_surface();
    te = boost::posix_time::microsec_clock::local_time();
    tdif = te - tb;
    debug_contact_time = tdif.total_milliseconds();
  }


  void atom_mask::get_expanded_asu_boundaries(scitbx::double3 &low,
    scitbx::double3 &high) const
  {
    low = this->expanded_box[0];
    high = this->expanded_box[1];
  }


  void atom_mask::get_asu_boundaries(scitbx::int3 &low,
    scitbx::int3 &high) const
  {
    low = this->asu_low;
    high = this->asu_high;
  }


  void atom_mask::get_expanded_asu_boundaries(scitbx::int3 &low,
    scitbx::int3 &high) const
  {
    low = scitbx::int3( this->data.accessor().origin() );
    high = scitbx::int3( this->data.accessor().last() );
  }

  inline scitbx::double3 conv_(const rvector3_t r)
  {
    return scitbx::double3( boost::rational_cast<double,int>(r[0]),
      boost::rational_cast<double,int>(r[1]),
      boost::rational_cast<double,int>(r[2]));
  }

  void atom_mask::determine_boundaries()
  {
    const scitbx::int3 n = this->grid_size();
    MMTBX_ASSERT( n[0]>0 && n[1]>0 && n[2]>0 );
    cctbx::sgtbx::asu::rvector3_t mn, mx;
    this->asu.box_corners(mn,mx);
    this->expanded_box[0] = conv_(mn);
    this->expanded_box[1] = conv_(mx);
    MMTBX_ASSERT( scitbx::ge_all(expanded_box[1], expanded_box[0]) );
    scitbx::mul(mn, n);
    scitbx::mul(mx, n);
    // inclusive: [imn, imx]
    this->asu_low = scitbx::floor(mn);
    this->asu_high = scitbx::ceil(mx); // asu boundaries
    MMTBX_ASSERT( scitbx::gt_all(this->asu_high, this->asu_low) );
    this->asu_high += scitbx::int3(1,1,1); // now typical C++: [imn,imx)
    // the following is only assumed in atoms_to_asu
    MMTBX_ASSERT( scitbx::le_all(this->asu_high, n+1) );
    MMTBX_ASSERT( scitbx::ge_all(this->asu_low, -n-1) );

    // expand asu by shrink_truncation_radius
    const scitbx::af::tiny<double,6> rcell = cell.reciprocal_parameters();
    const scitbx::double3 rp(rcell[0], rcell[1], rcell[2]);
    // TODO: try different constant 1.005
    scitbx::double3 shrink_box = rp * (shrink_truncation_radius*1.05);
    this->expanded_box[0] -= shrink_box;
    this->expanded_box[1] += shrink_box;
    scitbx::mul( shrink_box, n );
    // expanded asu boundaries
    scitbx::int3 emn, emx;
    emn = this->asu_low + ifloor( -shrink_box );
    emx = this->asu_high + iceil( shrink_box ) - scitbx::int3(1,1,1);
    for(short idim=0; idim<3; ++idim)
      if( emx[idim]<asu_high[idim] )
        ++emx[idim];
    MMTBX_ASSERT( scitbx::ge_all(emx, emn) && scitbx::ge_all(emx,this->asu_high)
      && scitbx::le_all(emn, this->asu_low) );
    this->data.resize(grid_t(emn,emx));
  }


  void atom_mask::atoms_to_asu(
    const coord_array_t & sites_frac,
    const double_array_t & atom_radii)
  {
    MMTBX_ASSERT(sites_frac.size() == atom_radii.size());
    this->asu_atoms.clear();
    const scitbx::af::tiny<double,6> rcell = cell.reciprocal_parameters();
    const scitbx::double3 rp(rcell[0], rcell[1], rcell[2]);

    std::vector< scitbx::tiny3 > cells;
    this->asu.get_adjacent_cells(cells);

    scitbx::af::shared< cctbx::sgtbx::rt_mx > symops_ = group.all_ops();
    scitbx::af::const_ref< cctbx::sgtbx::rt_mx > symops = symops_.const_ref();
    const size_t order = symops.size();
    MMTBX_ASSERT( order == group.order_z() );

    const signed char n_corners = 2;
    scitbx::double3 asu_box[n_corners];
    this->get_expanded_asu_boundaries(asu_box[0], asu_box[1]);
    for(size_t iat=0; iat<sites_frac.size(); ++iat)
    {
      const scitbx::double3 at = sites_frac[iat];
      const double at_r =  atom_radii[iat];
      const double radius = at_r + solvent_radius;

      MMTBX_ASSERT( radius >= 0.0 );
      scitbx::double3 box = rp * (radius*1.05);
      scitbx::vec3<int> ibox = ifloor(box) + 2;
      for(short idim=0; idim<3; ++idim)
        if( ibox[idim] < 1 )
          ibox[idim] = 1;

      for(register size_t isym=0; isym<order; ++isym)
      {
        scitbx::double3 sym_at = symops[isym]*at;
        sym_at -= scitbx::floor(sym_at);
        scitbx::vec3<int> cell;
        // In previous version there was mapping onto full cell
        // so only atoms in the adjacent [1,+1] cells needed
        // to be tested for the intersection with the asu.
        // Because if the atom in the farther cell [+-n]
        // is intersecting with the asu then so does the
        // closer one.
        // In this version, there is no mapping, so need
        // to collect all intersecting atoms
        for(cell[0] = -ibox[0]; cell[0]<=ibox[0]; ++cell[0])
        {
          for(cell[1] = -ibox[1]; cell[1]<=ibox[1]; ++cell[1])
          {
            for(cell[2] = -ibox[2]; cell[2]<=ibox[2]; ++cell[2])
            {
              const scitbx::double3 sym_at_cell = sym_at + cell;
              cctbx::sgtbx::asu::intersection_kind  intersection
                = cctbx::sgtbx::asu::none;
              const scitbx::double3 atom_box[n_corners] = { sym_at_cell - box,
                sym_at_cell + box };
              CCTBX_ASSERT( scitbx::ge_all(atom_box[1], atom_box[0]) );

              // TODO: improve intersection check to minimize
              // number of intersecting atoms
              if( scitbx::ge_all(atom_box[1], asu_box[0])
                  && scitbx::le_all(atom_box[0], asu_box[1]) )
                intersection =  partially;

              if( intersection != cctbx::sgtbx::asu::none )
              {
                // due to that there is no mapping onto full cell
                // all atoms intersecting with the asu are required
                this->asu_atoms.push_back(atom_t(sym_at_cell,at_r));
                // TODO: possible optimization?
                // test for fully intersecting atom:
                //   is_inside( every corner of atom box)
                //   and the atom box needs to be expanded by
                //   shrink_truncation_radius
                // then break out from the symmetry loop
              }
            } // cell[2] loop
          } // cel[1] loop
        } // cell[0]
      }
    }
  }


  namespace {
    typedef scitbx::af::c_grid_padded<3> padded_grid_t;
    typedef scitbx::af::versa<double, padded_grid_t >
      versa_3d_padded_real_array;
    typedef scitbx::af::versa<std::complex<double>, padded_grid_t >
      versa_3d_padded_complex_array;
  }


  scitbx::af::shared< std::complex<double> > atom_mask::structure_factors(
    const scitbx::af::const_ref< cctbx::miller::index<> > &indices )
  {
    const mask_array_t &msk = this->get_mask();
    const scitbx::int3 grid_full_cell = this->grid_size();
    scitbx::fftpack::real_to_complex_3d<double> fft(grid_full_cell);

    // m_real : physical dims, n_real - focus dims
    const scitbx::int3 mdim = fft.m_real(), ndim = fft.n_real();
    MMTBX_ASSERT( ndim == grid_full_cell );
    MMTBX_ASSERT( scitbx::le_all( ndim, mdim ) );
    const padded_grid_t pad( mdim, ndim );
    // TODO: optimize?
    // padded_real could be a very huge array; filling it with 0 could be the
    // slowest part of this routine apart from fft
    versa_3d_padded_real_array padded_real(pad, 0.0);
    // convert non-padded asu-sized integer mask to padded full-cell
    // sized real array
    scitbx::af::ref<double, padded_grid_t > prref = padded_real.ref();
    scitbx::af::const_ref<data_type, grid_t > mskref = msk.const_ref();
    scitbx::int3 imn, imx;
    this->get_asu_boundaries(imn, imx); // [imn, imx)
    std::vector<long> kc(imx[2]-imn[2]);
    long kk = imn[2];
    for(std::vector<long>::iterator it=kc.begin(); it!=kc.end(); ++it, ++kk)
    {
      long k_c = kk % ndim[2];
      if( k_c <0 )
        k_c += ndim[2];
      *it = k_c;
    }
    MMTBX_ASSERT( kk==imx[2] );
    scitbx::vec3<size_t> cn = prref.accessor().all();
    scitbx::vec3<long> n( mskref.accessor().all() );
    const long nynz = n[1]*n[2], cnynz = cn[1]*cn[2];
    long j_c_b = imn[1] % ndim[1];
    if( j_c_b<0 )
      j_c_b += ndim[1];
    j_c_b *= cn[2];
    const long j_c_e = ndim[1] * cn[2];
    const data_type *p_asu_x = &mskref(imn);
    for(long i=imn[0]; i<imx[0]; ++i, p_asu_x += nynz )
    {
      register long i_c = i % ndim[0];
      if( i_c<0 )
        i_c += ndim[0];
      const long ind_c_x = i_c*cnynz;
      long ind_c_y = ind_c_x + j_c_b;
      const long ind_c_y_e = ind_c_x + j_c_e;
      const data_type *const p_asu_y_e = p_asu_x + (imx[1]-imn[1])*n[2];
      for(const data_type *p_asu_y = p_asu_x; p_asu_y!=p_asu_y_e;
        p_asu_y += n[2], ind_c_y += cn[2] )
      {
        if( ind_c_y == ind_c_y_e )
          ind_c_y = ind_c_x;
        const data_type *p_asu_z = p_asu_y;
        for(std::vector<long>::const_iterator k_c=kc.begin(); k_c!=kc.end();
          ++k_c, ++p_asu_z)
        {
          const data_type t = *p_asu_z;
          MMTBX_ASSERT( t>=0 && t<mark );
          if( t>0 )
            prref[ ind_c_y + *k_c ] = t;
        }
      }
    }

    boost::posix_time::ptime
      tb = boost::posix_time::microsec_clock::local_time(), te;
    fft.forward(padded_real); // in-place forward FFT
    const padded_grid_t pad_complex( fft.n_complex(), fft.n_complex() );
    versa_3d_padded_complex_array result(padded_real.handle(), pad_complex );
    boost::posix_time::time_duration tdif =
      boost::posix_time::microsec_clock::local_time() - tb;
    debug_fft_time = tdif.total_milliseconds();

    const cctbx::maptbx::structure_factors::from_map<double> the_from_map(
      group,
      false, // anomalous flag
      indices,
      result.const_ref(),
      true); // conjugate_flag
    const double scale = cell.volume()
      / ( ndim.product() * static_cast<double>(group.order_z()) );
    // result.size() could be approx 1000 * the_from_map.data().size()
    // this does not work :( the_from_map.data() *= scale;
    scitbx::af::ref< std::complex<double> > dref = the_from_map.data().ref();
    // scaling takes no time
    for(scitbx::af::ref< std::complex<double> >::iterator i=dref.begin();
      i!=dref.end(); ++i)
      (*i) *= scale;
    return the_from_map.data();
  }


  void atom_mask::determine_gridding(cctbx::sg_vec3 &grid, double resolution,
    double factor) const
  {
    MMTBX_ASSERT( factor > 0.0 );
    double step = resolution/factor;
    if(step < 0.15)
      step = 0.15;
    step = std::min(0.8, step);
    const double d_min = 2.0*step, resolution_factor = 0.5;
    const cctbx::sgtbx::search_symmetry_flags use_all(true,
        0,
        true,
        true,
        true);
    grid = cctbx::maptbx::determine_gridding<int>(cell, d_min,
      resolution_factor, use_all, group.type());
  }


  void atom_mask::compute_accessible_surface(const atom_array_t & atoms)
  {
    cctbx::uctbx::unit_cell const& unit_cell = this->cell;
    // Severe code duplication: cctbx/maptbx/average_densities.h
    af::ref<data_type, grid_t > data_ref = data.ref();
    scitbx::int3 n_g = this->grid_size();
    const int nx = n_g[0];
    const int ny = n_g[1];
    const int nz = n_g[2];
    MMTBX_ASSERT( nx>0 && ny>0 && nz>0 );
    const f_t mr1= static_cast<f_t>(unit_cell.metrical_matrix()[0]); // a*a
    const f_t mr5= static_cast<f_t>(unit_cell.metrical_matrix()[1]); // b*b
    const f_t mr9= static_cast<f_t>(unit_cell.metrical_matrix()[2]); // c*c
    // a*b*cos(gamma)
    const f_t mr2= static_cast<f_t>(unit_cell.metrical_matrix()[3]);
    // a*c*cos(beta)
    const f_t mr3= static_cast<f_t>(unit_cell.metrical_matrix()[4]);
    // c*b*cos(alpha)
    const f_t mr6= static_cast<f_t>(unit_cell.metrical_matrix()[5]);
    const f_t tmr2 = mr2*2; //2*a*b*cos(gamma);
    const f_t tmr3 = mr3*2; //2*a*c*cos(beta);
    const f_t tmr6 = mr6*2; //2*b*c*cos(alpha);
    const f_t sx = 1/static_cast<f_t>(nx);
    const f_t tsx= sx*2;
    const f_t sxsq=mr1*sx*sx;
    const f_t sy = 1/static_cast<f_t>(ny);
    const f_t tsy= sy*2;
    const f_t sysq=mr5*sy*sy;
    const f_t sz = 1/static_cast<f_t>(nz);
    const f_t tsz= sz*2;
    const f_t szsq=mr9*sz*sz;
    const f_t w1=mr1*sx*tsx; const f_t w4=mr5*sy*tsy;
    const f_t w2=mr2*sx*tsy; const f_t w5=mr6*sy*tsz;
    const f_t w3=mr3*sx*tsz; const f_t w6=mr9*sz*tsz;
    const f_t tsxg1=tsx*mr1; const f_t tsyg4=tsy*mr2; const f_t tszg3=tsz*mr3;
    const f_t tsxg4=tsx*mr2; const f_t tsyg5=tsy*mr5; const f_t tszg8=tsz*mr6;
    const f_t tsxg7=tsx*mr3; const f_t tsyg8=tsy*mr6; const f_t tszg9=tsz*mr9;
    f_t rp[3];
    for(unsigned i=0;i<3;i++) {
      rp[i] = static_cast<f_t>(unit_cell.reciprocal_parameters()[i]);
    }
    scitbx::int3 asu_min, asu_max;
    this->get_expanded_asu_boundaries(asu_min, asu_max);
    const scitbx::vec3<long> sz_asu(data_ref.accessor().all());
    const long sz_yz = sz_asu[1]*sz_asu[2];
    const long asu_off = sz_yz* asu_min[0] + sz_asu[2]* asu_min[1] + asu_min[2];
    for(std::size_t i_site=0;i_site<atoms.size();i_site++) {
      cctbx::fractional<> const& site = atoms[i_site].first;
      const f_t xfi=static_cast<f_t>(site[0]);
      const f_t yfi=static_cast<f_t>(site[1]);
      const f_t zfi=static_cast<f_t>(site[2]);
      const f_t atmrad = atoms[i_site].second;
      MMTBX_ASSERT( atmrad >= 0.0 );
      const f_t cutoff=static_cast<f_t>(atmrad+solvent_radius);
      const f_t radsq=static_cast<f_t>(atmrad*atmrad);
      const f_t cutoffsq=cutoff*cutoff;
      const f_t coas = cutoff*rp[0];
      int x1box=ifloor(nx*(xfi-coas));
      int x2box=1+iceil(nx*(xfi+coas));
      const f_t cobs = cutoff*rp[1];
      int y1box=ifloor(ny*(yfi-cobs));
      int y2box=1+iceil(ny*(yfi+cobs));
      const f_t cocs = cutoff*rp[2];
      int z1box=ifloor(nz*(zfi-cocs));
      int z2box=1+iceil(nz*(zfi+cocs));
      x1box = std::max( x1box, static_cast<int>(asu_min[0]));
      y1box = std::max( y1box, static_cast<int>(asu_min[1]));
      z1box = std::max( z1box, static_cast<int>(asu_min[2]));
      x2box = std::min( x2box, static_cast<int>(asu_max[0]));
      y2box = std::min( y2box, static_cast<int>(asu_max[1]));
      z2box = std::min( z2box, static_cast<int>(asu_max[2]));
      const f_t sxbcen=xfi-x1box*sx;
      const f_t sybcen=yfi-y1box*sy;
      const f_t szbcen=zfi-z1box*sz;
      const f_t distsm=mr1*sxbcen*sxbcen+mr5*sybcen*sybcen+mr9*szbcen*szbcen
            +tmr2*sxbcen*sybcen+tmr3*sxbcen*szbcen+tmr6*sybcen*szbcen;
      const f_t w7=tsxg1*sxbcen+tsxg4*sybcen+tsxg7*szbcen;
      const f_t w8=tsyg4*sxbcen+tsyg5*sybcen+tsyg8*szbcen;
      const f_t w9=tszg3*sxbcen+tszg8*sybcen+tszg9*szbcen;
      register f_t distsx = distsm;
      register f_t s1xx = sxsq - w7;
      register f_t s1xy = sysq - w8;
      register f_t s1xz = szsq - w9;
      const long ind_x_max = sz_yz*x2box - asu_off;
      const long yba1 = y1box*sz_asu[2], yba2 = y2box*sz_asu[2];
      for(register long ind_x = x1box*sz_yz - asu_off ; ind_x<ind_x_max;
        ind_x+=sz_yz)
      {
        register f_t s2yz = s1xz;
        register f_t s2_incr = s1xy;
        register f_t s2 = distsx;
        const long ind_y_max = ind_x + yba2;
        for(register long ind_y=ind_x+yba1; ind_y<ind_y_max; ind_y+=sz_asu[2])
        {
          register f_t s3_incr = s2yz;
          register f_t dist = s2;
          const long ind_z_max = ind_y+z2box;
          for(register long ind_z=ind_y+z1box; ind_z<ind_z_max; ++ind_z)
          {
            const f_t dist_c  = dist;
            if( dist_c < cutoffsq )
            {
              // ind_z equals scitbx::int3(kx,ky,kz)
              data_type& dr = data_ref[ind_z];
              if (dist_c < radsq)
                dr =  0;
              else if(dr>0)
              {
                dr = -dr;
              }
            }
            dist += s3_incr;
            s3_incr += w6;
          } // z-box loop
          s2 += s2_incr;
          s2_incr += w4;
          s2yz += w5;
        } // y-box loop
        distsx += s1xx;
        s1xx += w1;
        s1xy += w2;
        s1xz += w3;
      } // x-box loop
    } //atom loop
    return;
  }


  typedef af::const_ref< cctbx::sgtbx::rt_mx > symop_array;

  void find_neighbors(
      std::vector<long> &table,
      cctbx::uctbx::unit_cell const& unit_cell,
      af::c_grid<3>::index_type const& gridding_n_real,
      const scitbx::vec3<long> &na,
      double shrink_truncation_radius)
  {
    int low[3];
    int high[3];
    for(unsigned i=0;i<3;i++) {
      double x = shrink_truncation_radius
               * unit_cell.reciprocal_parameters()[i]
               * gridding_n_real[i];
      low[i] = ifloor(-x);
      high[i] = iceil(x);
    }
    const int n0 = static_cast<int>(gridding_n_real[0]);
    const int n1 = static_cast<int>(gridding_n_real[1]);
    const int n2 = static_cast<int>(gridding_n_real[2]);
    const f_t shrink_truncation_radius_sq = shrink_truncation_radius
                                    * shrink_truncation_radius;
    cctbx::fractional<f_t> frac;
    for(int p0=low[0];p0<=high[0];p0++)
    {
      frac[0] = static_cast<f_t>(p0) / n0;
      for(int p1=low[1];p1<=high[1];p1++)
      {
        frac[1] = static_cast<f_t>(p1) / n1;
        for(int p2=low[2];p2<=high[2];p2++)
        {
          frac[2] = static_cast<f_t>(p2) / n2;
          f_t dist_sq = unit_cell.length_sq(frac);
          if (dist_sq < shrink_truncation_radius_sq)
            table.push_back(p0*(na[1]*na[2]) + p1*na[2]  + p2);
        } // z
      } // y
    } // x
  }


  void atom_mask::compute_contact_surface()
  {
    typedef double f_t;
    cctbx::uctbx::unit_cell const& unit_cell = this->cell;

    register size_t nsolv = 0;
    af::ref<data_type, asu_grid_t > data_ref = data.ref();
    std::size_t data_size = data_ref.size();
    if(shrink_truncation_radius == 0) {
      for(std::size_t ilxyz=0;ilxyz<data_size;ilxyz++)
      {
        data_type &d = data_ref[ilxyz];
        if( d < 0 || d == mark )
          d = 0;
        nsolv += d;
      }
      contact_surface_fraction = accessible_surface_fraction
        = static_cast<double>(nsolv) / this->grid_size_1d();
      return;
    }

    af::versa<data_type, asu_grid_t > datacopy = data.deep_copy();
    const af::const_ref<data_type, asu_grid_t > datacopy_ref
      = datacopy.const_ref();
    const scitbx::vec3<long> n(datacopy_ref.accessor().all());
    std::vector<long> neighbors;
    find_neighbors(neighbors, unit_cell, this->grid_size(), n,
      shrink_truncation_radius);
    MMTBX_ASSERT(neighbors.size()>0U);
    size_t n_access=0;
    const data_type *datacopy_ptr = datacopy_ref.begin();
    for(data_type *data_ptr = data_ref.begin(); data_ptr!=data_ref.end();
      ++data_ptr, ++datacopy_ptr)
    {
      const data_type d_copy = *datacopy_ptr;
      data_type &dr = *data_ptr;
      if( d_copy == mark || d_copy == -mark )
        dr = 0;
      else if( d_copy >0 )
        n_access += static_cast<size_t>( d_copy );
      else if( d_copy < 0 )
      {
        for(std::vector<long>::const_iterator neighbor=neighbors.begin();
          neighbor!=neighbors.end(); ++neighbor)
        {
          // neighbor could be outside the asu, but must be inside the
          // expanded asu
          if( datacopy_ptr[*neighbor] > 0)
          {
            dr = -dr;
            MMTBX_ASSERT( dr > 0 );
            goto end_of_neighbors_loop;
          }
        }
        dr = 0;
        end_of_neighbors_loop:;
      }
      nsolv += dr;
    } // data_ref array
    // currently data is not padded 3-D array, data.size is correct here
    contact_surface_fraction =  static_cast<double>(nsolv)/this->grid_size_1d();
    accessible_surface_fraction = static_cast<double>(n_access)
      / this->grid_size_1d();
  }

}} // namespace mmtbx::masks

