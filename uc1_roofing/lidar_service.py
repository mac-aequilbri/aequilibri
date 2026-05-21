"""
UC1 LiDAR & Roof Analysis Service
==================================
Analyses a property using:
  1. Perimeter & guttering   — calculated from the building polygon (instant, no deps)
  2. Multi-structure detection — queries local BuildingFootprint DB
  3. Elevation / heights      — Geoscience Australia ELVIS WCS (requires internet)
  4. Scaffolding estimate     — derived from eave height (>3 m triggers requirement)
  5. Solar panel flag         — manual toggle for now; ML hook ready

External APIs used (free, no key required):
  ELVIS datasets  : https://elvis2.ga.gov.au/api/v1/datasets
  GA WCS (1m DEM) : https://services.ga.gov.au/gis/services/DEM_LiDAR_1m/MapServer/WCSServer
  GA WCS (DTM)    : https://services.ga.gov.au/gis/services/DEM_SRTM_1Second_Hydro_Enforced/MapServer/WCSServer

All methods fail gracefully — if LiDAR is unavailable the service returns
estimated values based on storey count with clear data_source labels.
"""

import json, math, struct, io, logging, time
import urllib.request, urllib.parse, urllib.error
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# ─── API endpoints ────────────────────────────────────────────────────────────
ELVIS_API   = "https://elvis2.ga.gov.au/api/v1/datasets"
GA_WCS_DSM  = ("https://services.ga.gov.au/gis/services/"
               "DEM_LiDAR_1m/MapServer/WCSServer")
GA_WCS_DTM  = ("https://services.ga.gov.au/gis/services/"
               "DEM_SRTM_1Second_Hydro_Enforced/MapServer/WCSServer")

# Scaffolding is required when eave height exceeds this threshold (metres)
SCAFFOLD_HEIGHT_THRESHOLD = 3.0
# Buffer around building footprint when fetching elevation (degrees)
ELEV_BUFFER_DEG = 0.0002   # ≈ 20 m


# ─── Geometry helpers (pure Python, no external deps) ─────────────────────────

def _haversine_distance(lat1, lon1, lat2, lon2):
    """Return distance in metres between two lat/lon points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2)**2
    return 2 * R * math.asin(math.sqrt(a))


def calculate_perimeter(polygon_coords: list) -> float:
    """
    Calculate the perimeter of a building polygon in metres.

    polygon_coords: list of [lat, lng] or [lng, lat] pairs
                    (the function auto-detects order by value range)
    Returns: perimeter in metres (float)
    """
    if len(polygon_coords) < 3:
        return 0.0

    pts = [(float(p[0]), float(p[1])) for p in polygon_coords]

    # Auto-detect coordinate order: lat is -90..90, lon is -180..180
    # If first value looks like a longitude (>90 or <-90) swap it
    if abs(pts[0][0]) > 90:
        pts = [(p[1], p[0]) for p in pts]   # swap to (lat, lon)

    # Close ring if not already closed
    if pts[0] != pts[-1]:
        pts.append(pts[0])

    total = 0.0
    for i in range(len(pts) - 1):
        total += _haversine_distance(pts[i][0], pts[i][1],
                                     pts[i+1][0], pts[i+1][1])
    return round(total, 2)


def _polygon_bbox(polygon_coords: list):
    """Return (min_lat, min_lon, max_lat, max_lon) from polygon."""
    pts = [(float(p[0]), float(p[1])) for p in polygon_coords]
    if abs(pts[0][0]) > 90:
        pts = [(p[1], p[0]) for p in pts]
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    return min(lats), min(lons), max(lats), max(lons)


def _point_in_polygon(lat, lon, polygon_coords):
    """Ray-casting point-in-polygon test."""
    pts = [(float(p[0]), float(p[1])) for p in polygon_coords]
    if abs(pts[0][0]) > 90:
        pts = [(p[1], p[0]) for p in pts]
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if ((yi > lon) != (yj > lon)) and (lat < (xj - xi) * (lon - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


# ─── Multi-structure detection ────────────────────────────────────────────────

def detect_all_structures(lat: float, lng: float, radius_m: float = 30.0) -> list:
    """
    Query the local BuildingFootprint DB for all structures within radius_m
    of the given point. Returns a list of dicts with area_sqm and centroid.

    This uses the Microsoft ML building footprints already imported into
    the UC1 database — no network call needed.
    """
    try:
        from uc1_roofing.models import BuildingFootprint

        # Convert radius to rough degree offset for initial bbox filter
        deg_offset = radius_m / 111_000
        qs = BuildingFootprint.objects.filter(
            min_lat__lte=lat + deg_offset,
            max_lat__gte=lat - deg_offset,
            min_lon__lte=lng + deg_offset,
            max_lon__gte=lng - deg_offset,
        )

        structures = []
        for fp in qs:
            dist = _haversine_distance(lat, lng, fp.centroid_lat, fp.centroid_lon)
            if dist <= radius_m:
                try:
                    coords = json.loads(fp.geometry)
                except Exception:
                    coords = []
                structures.append({
                    'id': fp.id,
                    'area_sqm': round(fp.area_sqm, 1),
                    'centroid_lat': fp.centroid_lat,
                    'centroid_lon': fp.centroid_lon,
                    'distance_m': round(dist, 1),
                    'perimeter_m': calculate_perimeter(coords),
                    'geometry': coords,
                })
        structures.sort(key=lambda s: s['distance_m'])
        return structures

    except Exception as e:
        log.warning("detect_all_structures failed: %s", e)
        return []


# ─── ELVIS LiDAR availability check ──────────────────────────────────────────

def check_lidar_coverage(lat: float, lng: float) -> dict:
    """
    Query ELVIS to see if 1m LiDAR data exists for this location.
    Returns {'available': bool, 'datasets': list, 'resolution_m': float}
    """
    buf = 0.005
    bbox = f"{lng-buf},{lat-buf},{lng+buf},{lat+buf}"
    url = f"{ELVIS_API}?bbox={bbox}&type=lidar&state=QLD"

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'aequilibri-platform/1.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        datasets = data if isinstance(data, list) else data.get('results', [])
        lidar_sets = [d for d in datasets if 'lidar' in str(d).lower() or
                      'd' in str(d.get('type', '')).lower()]

        return {
            'available': len(lidar_sets) > 0,
            'datasets': lidar_sets[:3],
            'resolution_m': 1.0 if lidar_sets else None,
        }
    except Exception as e:
        log.info("ELVIS coverage check: %s", e)
        return {'available': False, 'datasets': [], 'resolution_m': None}


# ─── Elevation raster fetch (GA WCS) ─────────────────────────────────────────

def _fetch_wcs_raster(wcs_url: str, bbox_str: str,
                      width: int = 60, height: int = 60) -> Optional[np.ndarray]:
    """
    Fetch a GeoTIFF elevation raster from a Geoscience Australia WCS endpoint.
    Returns numpy array of elevation values in metres, or None on failure.
    """
    params = {
        'SERVICE': 'WCS', 'VERSION': '1.0.0', 'REQUEST': 'GetCoverage',
        'COVERAGE': '1',
        'CRS': 'EPSG:4326',
        'BBOX': bbox_str,
        'WIDTH': str(width),
        'HEIGHT': str(height),
        'FORMAT': 'GeoTIFF_Float',
    }
    url = wcs_url + '?' + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'aequilibri-platform/1.0'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        return _parse_geotiff(raw)
    except Exception as e:
        log.info("WCS fetch failed (%s): %s", wcs_url[:60], e)
        return None


def _parse_geotiff(data: bytes) -> Optional[np.ndarray]:
    """
    Minimal GeoTIFF parser — extracts the elevation float32 raster.
    Handles standard single-band float GeoTIFF files (no external deps).
    """
    if len(data) < 8:
        return None

    # Read byte order marker
    endian = '<' if data[:2] == b'II' else '>'
    magic = struct.unpack_from(f'{endian}H', data, 2)[0]
    if magic not in (42, 43):
        log.warning("Not a TIFF file (magic=%d)", magic)
        return None

    try:
        buf = io.BytesIO(data)
        # First IFD offset
        buf.seek(4)
        ifd_offset = struct.unpack(f'{endian}I', buf.read(4))[0]

        # Read IFD entries
        buf.seek(ifd_offset)
        n_entries = struct.unpack(f'{endian}H', buf.read(2))[0]

        tags = {}
        for _ in range(n_entries):
            tag_raw = buf.read(12)
            tag, dtype, count = struct.unpack(f'{endian}HHI', tag_raw[:8])
            value_raw = tag_raw[8:]

            # For small values, read inline; for large, it's an offset
            if dtype == 3:   # SHORT
                val = struct.unpack(f'{endian}H', value_raw[:2])[0]
            elif dtype == 4:  # LONG
                val = struct.unpack(f'{endian}I', value_raw)[0]
            elif dtype == 5:  # RATIONAL
                pos = buf.tell()
                offset = struct.unpack(f'{endian}I', value_raw)[0]
                buf.seek(offset)
                num, den = struct.unpack(f'{endian}II', buf.read(8))
                val = num / den if den else 0
                buf.seek(pos)
            else:
                val = struct.unpack(f'{endian}I', value_raw)[0]
            tags[tag] = (count, val)

        width  = tags.get(256, (1, 1))[1]
        height = tags.get(257, (1, 1))[1]
        bits   = tags.get(258, (1, 32))[1]
        strip_offset = tags.get(273, (1, 0))[1]
        strip_bytes  = tags.get(279, (1, width * height * 4))[1]

        buf.seek(strip_offset)
        raw_pixels = buf.read(strip_bytes)

        dtype_np = np.float32 if bits == 32 else np.float64
        arr = np.frombuffer(raw_pixels, dtype=dtype_np).reshape(height, width)

        # Replace nodata (-9999 or very negative) with nan
        arr = arr.astype(np.float64)
        arr[arr < -1000] = np.nan
        return arr

    except Exception as e:
        log.warning("GeoTIFF parse error: %s", e)
        return None


# ─── Elevation analysis ───────────────────────────────────────────────────────

def _analyze_elevation_arrays(dsm: np.ndarray, dtm: np.ndarray,
                               polygon_coords: list) -> dict:
    """
    Compute nDSM (building height above ground) and extract roof features.
    dsm: Digital Surface Model (includes buildings)
    dtm: Digital Terrain Model (bare earth)
    Both arrays should be the same shape.
    """
    ndsm = dsm - dtm  # normalized = height above ground

    # Mask to polygon (rough bbox mask — full ray-cast would need pixel coords)
    # For now use full array; polygon mask refinement added in production
    roof_heights = ndsm[ndsm > 1.0]   # anything >1m above ground = building

    if len(roof_heights) == 0:
        return {
            'ridge_height_m': None,
            'eave_height_m': None,
            'height_range_m': None,
            'data_source': 'lidar_no_data',
        }

    ridge_h = float(np.nanpercentile(roof_heights, 95))  # 95th pct = ridge
    eave_h  = float(np.nanpercentile(roof_heights, 10))  # 10th pct = eave

    return {
        'ridge_height_m': round(ridge_h, 2),
        'eave_height_m': round(eave_h, 2),
        'height_range_m': round(ridge_h - eave_h, 2),
        'data_source': 'lidar_1m',
    }


def _estimate_heights_from_storeys(storeys: int = 1) -> dict:
    """Fallback height estimates when LiDAR is unavailable."""
    eave  = {1: 2.7, 2: 5.5, 3: 8.5}.get(storeys, 2.7)
    ridge = eave + 2.0   # typical Queensland roof pitch adds ~2m
    return {
        'ridge_height_m': ridge,
        'eave_height_m': eave,
        'height_range_m': round(ridge - eave, 2),
        'data_source': 'estimated_from_storeys',
    }


# ─── Scaffolding logic ────────────────────────────────────────────────────────

def assess_scaffolding(eave_height_m: float, perimeter_m: float) -> dict:
    """
    Queensland WHS scaffolding rules:
    - Required when fall distance > 3 m (eave height above 3 m)
    - Edge protection on all exposed faces
    Returns a scaffolding assessment dict.
    """
    required = eave_height_m > SCAFFOLD_HEIGHT_THRESHOLD
    if not required:
        return {
            'required': False,
            'reason': f'Eave height {eave_height_m:.1f} m — below 3 m threshold',
            'estimated_linear_m': 0.0,
            'risk_level': 'low',
        }

    # Estimate linear metres of scaffolding (typically 80-100% of perimeter)
    linear_m = round(perimeter_m * 0.90, 1)
    return {
        'required': True,
        'reason': f'Eave height {eave_height_m:.1f} m — exceeds 3 m WHS threshold',
        'estimated_linear_m': linear_m,
        'risk_level': 'high' if eave_height_m > 6.0 else 'medium',
    }


# ─── Main entry point ─────────────────────────────────────────────────────────

def full_roof_analysis(lat: float, lng: float, polygon_coords: list,
                       storeys: int = 1,
                       solar_panels: bool = False,
                       solar_hw: bool = False) -> dict:
    """
    Orchestrates all roof analysis steps and returns a unified result dict.

    Always succeeds — LiDAR steps fail gracefully to estimates.

    Returns:
    {
      'perimeter_m':         float,
      'guttering_linear_m':  float,
      'ridge_height_m':      float,
      'eave_height_m':       float,
      'scaffolding':         dict,
      'structures':          list,
      'solar_panels':        bool,
      'solar_hw':            bool,
      'lidar_coverage':      str,   # 'full' | 'partial' | 'none' | 'estimated'
      'data_source':         str,
      'analysis_notes':      list[str],
    }
    """
    t0 = time.time()
    notes = []

    # ── 1. Perimeter & guttering ──────────────────────────────────────────────
    perimeter_m = calculate_perimeter(polygon_coords)
    guttering_m = round(perimeter_m * 0.85, 1)   # guttering not on all edges
    notes.append(f"Perimeter calculated from {len(polygon_coords)}-point polygon: {perimeter_m:.1f} m")

    # ── 2. Multi-structure detection ──────────────────────────────────────────
    structures = detect_all_structures(lat, lng, radius_m=40.0)
    n_structures = len(structures)
    if n_structures > 1:
        notes.append(f"{n_structures} structures detected on lot — estimator shows closest only")
    elif n_structures == 1:
        notes.append("Single structure confirmed on lot")
    else:
        notes.append("Structure count: using building footprint only")

    # ── 3. LiDAR elevation (try GA WCS) ──────────────────────────────────────
    height_data = None
    lidar_coverage = 'none'

    min_lat, min_lon, max_lat, max_lon = _polygon_bbox(polygon_coords)
    buf = ELEV_BUFFER_DEG
    bbox_str = f"{min_lon-buf},{min_lat-buf},{max_lon+buf},{max_lat+buf}"

    dsm = _fetch_wcs_raster(GA_WCS_DSM, bbox_str)
    if dsm is not None:
        dtm = _fetch_wcs_raster(GA_WCS_DTM, bbox_str)
        if dtm is not None and dsm.shape == dtm.shape:
            height_data = _analyze_elevation_arrays(dsm, dtm, polygon_coords)
            lidar_coverage = 'full'
            notes.append("Elevation data retrieved from GA 1m LiDAR WCS")
        else:
            # Fall back to DSM only (less accurate but still useful)
            roof_h = float(np.nanpercentile(dsm[dsm > 0], 90))
            ground_h = float(np.nanpercentile(dsm[dsm > 0], 5))
            height_data = {
                'ridge_height_m': round(roof_h - ground_h, 2),
                'eave_height_m': round((roof_h - ground_h) * 0.6, 2),
                'height_range_m': round((roof_h - ground_h) * 0.4, 2),
                'data_source': 'lidar_dsm_only',
            }
            lidar_coverage = 'partial'
            notes.append("Elevation from DSM only (DTM unavailable) — reduced accuracy")
    else:
        notes.append("LiDAR WCS unavailable — falling back to storey-based estimates")

    if height_data is None:
        height_data = _estimate_heights_from_storeys(storeys)
        lidar_coverage = 'estimated'

    # ── 4. Scaffolding assessment ─────────────────────────────────────────────
    eave_h = height_data.get('eave_height_m') or _estimate_heights_from_storeys(storeys)['eave_height_m']
    scaffolding = assess_scaffolding(eave_h, perimeter_m)
    notes.append(
        f"Scaffolding: {'REQUIRED' if scaffolding['required'] else 'not required'} "
        f"(eave {eave_h:.1f} m)"
    )

    # ── 5. Solar / rooftop equipment ─────────────────────────────────────────
    if solar_panels:
        notes.append("Solar panels flagged — remove & reinstall line item will be added")
    if solar_hw:
        notes.append("Solar hot water flagged — disconnection & reconnection allowance will be added")

    elapsed_ms = int((time.time() - t0) * 1000)

    return {
        'perimeter_m':        perimeter_m,
        'guttering_linear_m': guttering_m,
        'ridge_height_m':     height_data.get('ridge_height_m'),
        'eave_height_m':      height_data.get('eave_height_m'),
        'height_range_m':     height_data.get('height_range_m'),
        'scaffolding':        scaffolding,
        'structures':         structures,
        'structure_count':    max(n_structures, 1),
        'solar_panels':       solar_panels,
        'solar_hw':           solar_hw,
        'lidar_coverage':     lidar_coverage,
        'data_source':        height_data.get('data_source', 'unknown'),
        'analysis_notes':     notes,
        'elapsed_ms':         elapsed_ms,
    }
