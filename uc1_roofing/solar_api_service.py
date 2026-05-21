"""
UC1 Roofing — Google Solar API service layer.

Wraps the buildingInsights endpoint and derives boundary lengths
(ridge, valley, hip, eave, rake linear metres) from section geometry.

Used by:
  - views.solar_analyze (AJAX endpoint /uc1/api/solar-analyze/)
  - Eventually replaces GA WCS LiDAR as primary roof geometry source

Requires:
  GOOGLE_API_KEY env var (or settings.GOOGLE_API_KEY)
"""

import os
import json
import math
import time
import urllib.request
import urllib.parse
import logging

from uc1_roofing.services.paid_api_cache import (
    MEDIUM_TTL_SECONDS,
    NEGATIVE_TTL_SECONDS,
    clone_jsonable,
    get_cached,
    rounded_point,
    set_cached,
)

log = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    """Read Google API key from env var or Django settings.
    Checks multiple key names in priority order.
    """
    for env_name in ("GOOGLE_API_KEY", "GOOGLE_SOLAR_API_KEY", "GOOGLE_MAPS_API_KEY"):
        key = os.environ.get(env_name, "")
        if key:
            return key
    try:
        from django.conf import settings
        for attr in ("GOOGLE_API_KEY", "GOOGLE_SOLAR_API_KEY", "GOOGLE_MAPS_API_KEY"):
            key = getattr(settings, attr, "")
            if key:
                return key
    except Exception:
        pass
    return ""

SOLAR_INSIGHTS_URL = "https://solar.googleapis.com/v1/buildingInsights:findClosest"

AZIMUTH_LABELS = [
    (22.5,  "N"),   (67.5,  "NE"),  (112.5, "E"),   (157.5, "SE"),
    (202.5, "S"),   (247.5, "SW"),  (292.5, "W"),   (337.5, "NW"),
]

def _azimuth_label(deg: float) -> str:
    deg = deg % 360
    for threshold, label in AZIMUTH_LABELS:
        if deg < threshold:
            return label
    return "N"

def _slope_category(pitch_deg: float) -> str:
    if pitch_deg < 5:   return "flat"
    if pitch_deg < 15:  return "low"
    if pitch_deg < 25:  return "medium"
    if pitch_deg < 35:  return "steep"
    return "very steep"


# ─── Solar API call ───────────────────────────────────────────────────────────

def call_building_insights(lat: float, lng: float, quality: str = "LOW") -> dict:
    """
    Call Google Solar API buildingInsights:findClosest.
    quality: "LOW" = broadest coverage; "HIGH" = best accuracy
    Returns normalised dict or {"ok": False, "error": ...}
    """
    api_key = _get_api_key()
    if not api_key:
        return {"ok": False, "error": "GOOGLE_API_KEY not configured on server"}

    cache_payload = {**rounded_point(lat, lng), "quality": str(quality or "LOW").upper()}
    cached = get_cached("google_solar_building_insights", cache_payload)
    if cached is not None:
        result = clone_jsonable(cached)
        result["api_cache_hit"] = True
        return result

    params = urllib.parse.urlencode({
        "location.latitude":  lat,
        "location.longitude": lng,
        "requiredQuality":    quality,
        "key":                api_key,
    })
    url = f"{SOLAR_INSIGHTS_URL}?{params}"

    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            raw = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            msg = json.loads(body).get("error", {}).get("message", body[:200])
        except Exception:
            msg = body[:200]
        result = {"ok": False, "error": f"Solar API HTTP {e.code}: {msg}", "api_cache_hit": False}
        set_cached("google_solar_building_insights", cache_payload, result, NEGATIVE_TTL_SECONDS)
        return result
    except Exception as e:
        result = {"ok": False, "error": str(e), "api_cache_hit": False}
        set_cached("google_solar_building_insights", cache_payload, result, NEGATIVE_TTL_SECONDS)
        return result

    # Log top-level response keys and solarPotential keys to help diagnose
    # field name issues (especially for DETECTED_ARRAYS).
    sp = raw.get("solarPotential", {})
    log.info("Solar API response top-level keys: %s", list(raw.keys()))
    log.info("solarPotential keys: %s", list(sp.keys()))
    if "detectedArrays" in sp:
        log.info("detectedArrays: %s", sp["detectedArrays"])
    else:
        log.warning("detectedArrays NOT found in solarPotential — available keys: %s", list(sp.keys()))

    result = _parse_response(raw)
    result["api_cache_hit"] = False
    set_cached("google_solar_building_insights", cache_payload, result, MEDIUM_TTL_SECONDS)
    return result


def _parse_response(raw: dict) -> dict:
    sp = raw.get("solarPotential", {})
    if not sp:
        return {"ok": False, "error": "No solarPotential in Solar API response"}

    whole         = sp.get("wholeRoofStats", {})
    segments_raw  = sp.get("roofSegmentStats", [])
    imagery_date  = raw.get("imageryDate", {})
    date_str      = (f"{imagery_date.get('year','?')}-"
                     f"{str(imagery_date.get('month','?')).zfill(2)}")

    sections = []
    for seg in segments_raw:
        stats  = seg.get("stats", {})
        center = seg.get("center", {})
        bbox   = seg.get("boundingBox", {})
        pitch  = float(seg.get("pitchDegrees", 0))
        az     = float(seg.get("azimuthDegrees", 0))
        sections.append({
            "pitch_deg":      round(pitch, 1),
            "azimuth_deg":    round(az, 1),
            "area_m2":        round(float(stats.get("areaMeters2", 0)), 2),
            "ground_area_m2": round(float(stats.get("groundAreaMeters2", 0)), 2),
            "center": {"lat": float(center.get("latitude", 0)),
                       "lng": float(center.get("longitude", 0))},
            "bbox": {
                "sw": {"lat": float(bbox.get("sw", {}).get("latitude", 0)),
                       "lng": float(bbox.get("sw", {}).get("longitude", 0))},
                "ne": {"lat": float(bbox.get("ne", {}).get("latitude", 0)),
                       "lng": float(bbox.get("ne", {}).get("longitude", 0))},
            },
            "facing":         _azimuth_label(az),
            "slope_category": _slope_category(pitch),
        })

    sections.sort(key=lambda s: s["area_m2"], reverse=True)

    # ── Solar Panel Capacity (from solarPanels + solarPanelConfigs) ──────
    # solarPanels[] = every panel in the maximum optimal array.
    #   Each entry: { center:{latitude,longitude}, orientation, yearlyEnergyDcKwh, segmentIndex }
    #   len(solarPanels) == maxArrayPanelsCount (the full roof capacity).
    # solarPanelConfigs[] = stepped configurations from ~5 panels up to maxArrayPanelsCount.
    #   Each: { panelsCount, yearlyEnergyDcKwh, roofSegmentSummaries[] }
    # NOTE: additionalInsights=DETECTED_ARRAYS (for detecting *existing* panels) is a
    # premium feature not yet available in this key's tier. We therefore report
    # solar POTENTIAL (what could fit) rather than what is already installed.
    panels_raw     = sp.get("solarPanels", [])
    configs_raw    = sp.get("solarPanelConfigs", [])
    panel_h        = float(sp.get("panelHeightMeters", 1.879))
    panel_w        = float(sp.get("panelWidthMeters", 1.045))
    panel_cap_w    = float(sp.get("panelCapacityWatts", 400.0))
    panel_life_yr  = int(sp.get("panelLifetimeYears", 20))
    max_panels     = int(sp.get("maxArrayPanelsCount", 0))
    max_area_raw   = float(sp.get("maxArrayAreaMeters2", 0.0))
    max_shine_hr   = float(sp.get("maxSunshineHoursPerYear", 0.0))
    co2_kg_mwh     = float(sp.get("carbonOffsetFactorKgPerMwh", 0.0))

    panel_unit_m2  = round(panel_h * panel_w, 3)
    max_cap_kw     = round(max_panels * panel_cap_w / 1000, 2)

    # Maximum array (full roof)
    max_array_area_m2 = round(max_panels * panel_unit_m2, 1)

    # Pick a "typical" config — the one closest to 66% of max panels
    # (common residential install, avoids oversizing for small grids)
    typical_config = None
    if configs_raw:
        target = max_panels * 0.66
        typical_config = min(configs_raw, key=lambda c: abs(c.get("panelsCount", 0) - target))

    typ_panels   = typical_config.get("panelsCount", 0)     if typical_config else 0
    typ_kwh_yr   = typical_config.get("yearlyEnergyDcKwh", 0) if typical_config else 0
    typ_cap_kw   = round(typ_panels * panel_cap_w / 1000, 2)
    typ_area_m2  = round(typ_panels * panel_unit_m2, 1)

    # Max yearly energy (from last config, which has most panels)
    max_kwh_yr   = configs_raw[-1].get("yearlyEnergyDcKwh", 0) if configs_raw else 0

    return {
        "ok":              True,
        "error":           None,
        "imagery_date":    date_str,
        "imagery_quality": raw.get("imageryQuality", "UNKNOWN"),
        "section_count":   len(sections),
        "total_area_m2":   round(float(whole.get("areaMeters2", 0)), 2),
        "ground_area_m2":  round(float(whole.get("groundAreaMeters2", 0)), 2),
        "sections":        sections,
        # Solar capacity (from solarPanels + solarPanelConfigs)
        "panel_unit_m2":     panel_unit_m2,
        "panel_cap_w":       panel_cap_w,
        "panel_life_yr":     panel_life_yr,
        "max_shine_hr":      max_shine_hr,
        "co2_kg_mwh":        co2_kg_mwh,
        # Maximum array (full roof coverage)
        "max_panels":        max_panels,
        "max_area_m2":       max_array_area_m2,
        "max_cap_kw":        max_cap_kw,
        "max_kwh_yr":        round(max_kwh_yr, 0),
        # Typical install (~66% of max)
        "typ_panels":        typ_panels,
        "typ_area_m2":       typ_area_m2,
        "typ_cap_kw":        typ_cap_kw,
        "typ_kwh_yr":        round(typ_kwh_yr, 0),
        # Debug — lists every key inside solarPotential
        "_debug_sp_keys":    sorted(sp.keys()),
    }


# ─── Boundary length derivation ───────────────────────────────────────────────

def _bbox_dims_m(bbox: dict) -> tuple:
    """Return (width_m, height_m) of a bbox dict with sw/ne lat/lng."""
    sw_lat = bbox["sw"]["lat"];  sw_lng = bbox["sw"]["lng"]
    ne_lat = bbox["ne"]["lat"];  ne_lng = bbox["ne"]["lng"]
    cos_lat = math.cos(math.radians((sw_lat + ne_lat) / 2))
    return (abs(ne_lng - sw_lng) * cos_lat * 111320,
            abs(ne_lat - sw_lat) * 111320)


def _sections_adjacent(a: dict, b: dict, tol_m: float = 3.0) -> bool:
    cos_lat = math.cos(math.radians(a["center"]["lat"]))
    tol_lat = tol_m / 111320
    tol_lng = tol_m / (111320 * cos_lat)
    for axis, tol in [("lat", tol_lat), ("lng", tol_lng)]:
        def r(bbox):
            return (bbox["sw"][axis[-3:]], bbox["ne"][axis[-3:]]) if axis == "lat" \
                else (bbox["sw"]["lng"], bbox["ne"]["lng"])
        def rng(s): return (s["bbox"]["sw"]["lat"], s["bbox"]["ne"]["lat"]) \
                if axis == "lat" else (s["bbox"]["sw"]["lng"], s["bbox"]["ne"]["lng"])
        a_min, a_max = rng(a);  b_min, b_max = rng(b)
        if a_max + tol < b_min or b_max + tol < a_min:
            return False
    return True


def _junction_type(a: dict, b: dict) -> str:
    az_diff = abs(a["azimuth_deg"] - b["azimuth_deg"]) % 360
    if az_diff > 180: az_diff = 360 - az_diff
    if 135 <= az_diff:
        return "ridge" if (a["pitch_deg"] > 5 and b["pitch_deg"] > 5) else "eave"
    if az_diff < 45:   return "valley"
    return "hip"


def derive_boundary_lengths(sections: list) -> dict:
    """
    Derive ridge/valley/hip/eave/rake linear metres from Solar API sections.
    Returns dict with _lm fields + confidence + notes.
    """
    if not sections:
        return {"eave_lm": 0, "ridge_lm": 0, "valley_lm": 0,
                "hip_lm": 0, "rake_lm": 0, "perimeter_lm": 0,
                "confidence": "LOW", "notes": ["No sections available"]}

    notes = []
    section_widths = []

    for s in sections:
        w, h = _bbox_dims_m(s["bbox"])
        if w >= h:
            est_width, est_run = w, h
        else:
            est_width, est_run = h, w
        section_widths.append(round(est_width, 2))

    total_eave = sum(section_widths)
    ridge_lm = valley_lm = hip_lm = 0.0
    adjacency_count = 0

    for i in range(len(sections)):
        for j in range(i + 1, len(sections)):
            if not _sections_adjacent(sections[i], sections[j]):
                continue
            jtype = _junction_type(sections[i], sections[j])
            est_shared = min(section_widths[i], section_widths[j])
            adjacency_count += 1
            if jtype == "ridge":   ridge_lm  += est_shared
            elif jtype == "valley": valley_lm += est_shared
            elif jtype == "hip":    hip_lm    += est_shared

    # Rake: gable-end slope edges (2 per simple gable roof)
    has_ridge = adjacency_count > 0 and ridge_lm > 0
    rake_lm = 0.0
    if has_ridge and len(sections) >= 2:
        # Estimate one rake per end section with pitch > 5°
        end_sections = [s for s in sections if s["pitch_deg"] > 5][:2]
        for s in end_sections:
            w, h = _bbox_dims_m(s["bbox"])
            run = min(w, h)
            pitch_rad = math.radians(s["pitch_deg"])
            rake_lm += run / math.cos(pitch_rad) if pitch_rad < math.pi / 2 else run

    if adjacency_count == 0 and len(sections) > 1:
        notes.append(
            "No adjacent section pairs detected — boundary lengths are estimates only. "
            "Nearmap oblique imagery recommended for precise linear metre measurements."
        )

    confidence = "HIGH" if adjacency_count >= len(sections) - 1 else \
                 "MEDIUM" if adjacency_count > 0 else "LOW"

    return {
        "eave_lm":     round(total_eave, 2),
        "ridge_lm":    round(ridge_lm, 2),
        "valley_lm":   round(valley_lm, 2),
        "hip_lm":      round(hip_lm, 2),
        "rake_lm":     round(rake_lm, 2),
        "perimeter_lm": round(total_eave, 2),
        "confidence":  confidence,
        "notes":       notes,
    }


# ─── Full analysis (used by view) ─────────────────────────────────────────────

def full_solar_analysis(lat: float, lng: float,
                         storeys: int = 1,
                         solar_panels: bool = False,
                         solar_hw: bool = False) -> dict:
    """
    Run Solar API + boundary derivation.
    Returns a response dict suitable for the /uc1/api/solar-analyze/ endpoint.
    """
    t0 = time.time()

    solar = call_building_insights(lat, lng, quality="LOW")
    if not solar["ok"]:
        # Try HIGH quality as fallback (sometimes LOW quality parcels return errors
        # while HIGH succeeds for certain regions)
        solar = call_building_insights(lat, lng, quality="HIGH")

    if not solar["ok"]:
        return {
            "ok":            False,
            "error":         solar.get("error"),
            "data_source":   "solar_api_failed",
            "lidar_coverage": "unavailable",
            "elapsed_ms":    round((time.time() - t0) * 1000),
        }

    sections  = solar.get("sections", [])
    boundary  = derive_boundary_lengths(sections)

    # Dominant pitch (weighted by area)
    total_area = solar["total_area_m2"] or 1
    dominant_pitch = 0.0
    if sections:
        dominant_pitch = sum(s["pitch_deg"] * s["area_m2"] for s in sections) / total_area

    # Eave height estimate (Solar API does not provide height — use dominant pitch + ground run)
    eave_height_m  = None
    ridge_height_m = None
    if sections and boundary.get("eave_lm", 0) > 0:
        # Estimate ridge height from widest section: run ≈ eave_lm/2, ridge = run × tan(pitch)
        dominant_section = sections[0]
        w, h = _bbox_dims_m(dominant_section["bbox"])
        ground_run = min(w, h) / 2  # half-width as run approximation
        pitch_rad = math.radians(dominant_section["pitch_deg"])
        if pitch_rad > 0:
            ridge_height_m = round(ground_run * math.tan(pitch_rad), 2)
        # Eave height from storeys if unknown
        eave_height_m = {1: 2.7, 2: 5.5, 3: 8.5}.get(storeys, 2.7)

    # Scaffolding assessment
    scaffolding_required = (eave_height_m or 0) > 3.0
    scaffolding = {
        "required":           scaffolding_required,
        "estimated_linear_m": round(boundary.get("perimeter_lm", 0), 2),
        "risk_level":         "high" if (eave_height_m or 0) > 6.0 else
                              "medium" if scaffolding_required else "low",
        "reason":             f"Eave height ~{eave_height_m:.1f} m" if eave_height_m else
                              "Storey-based estimate",
    }

    analysis_notes = list(boundary.get("notes", []))
    if solar["imagery_quality"] not in ("HIGH",):
        analysis_notes.append(
            f"Solar API imagery quality: {solar['imagery_quality']} "
            f"(captured {solar['imagery_date']}) — HIGH quality recommended for precise measurements"
        )

    return {
        "ok":              True,
        "error":           None,
        "data_source":     "google_solar_api",
        "lidar_coverage":  "solar_api",
        "imagery_date":    solar["imagery_date"],
        "imagery_quality": solar["imagery_quality"],

        # Geometry
        "section_count":   solar["section_count"],
        "total_area_m2":   solar["total_area_m2"],
        "ground_area_m2":  solar["ground_area_m2"],
        "dominant_pitch_deg": round(dominant_pitch, 1),

        # Boundary lengths
        "perimeter_m":         boundary["perimeter_lm"],
        "guttering_linear_m":  boundary["eave_lm"],
        "eave_lm":             boundary["eave_lm"],
        "ridge_lm":            boundary["ridge_lm"],
        "valley_lm":           boundary["valley_lm"],
        "hip_lm":              boundary["hip_lm"],
        "rake_lm":             boundary["rake_lm"],
        "boundary_confidence": boundary["confidence"],

        # Heights (estimated)
        "eave_height_m":   eave_height_m,
        "ridge_height_m":  ridge_height_m,

        # Flags
        "solar_panels": solar_panels,
        "solar_hw":     solar_hw,
        "scaffolding":  scaffolding,

        # Solar capacity — from Google Solar API solarPanels + solarPanelConfigs
        "solar_panel_unit_m2":  solar.get("panel_unit_m2", 0.0),
        "solar_panel_cap_w":    solar.get("panel_cap_w", 0.0),
        "solar_panel_life_yr":  solar.get("panel_life_yr", 20),
        "solar_max_shine_hr":   solar.get("max_shine_hr", 0.0),
        "solar_co2_kg_mwh":     solar.get("co2_kg_mwh", 0.0),
        # Maximum array (full roof)
        "solar_max_panels":     solar.get("max_panels", 0),
        "solar_max_area_m2":    solar.get("max_area_m2", 0.0),
        "solar_max_cap_kw":     solar.get("max_cap_kw", 0.0),
        "solar_max_kwh_yr":     solar.get("max_kwh_yr", 0.0),
        # Typical residential install (~66% of max)
        "solar_typ_panels":     solar.get("typ_panels", 0),
        "solar_typ_area_m2":    solar.get("typ_area_m2", 0.0),
        "solar_typ_cap_kw":     solar.get("typ_cap_kw", 0.0),
        "solar_typ_kwh_yr":     solar.get("typ_kwh_yr", 0.0),
        # solar_panels_detected stays False — DETECTED_ARRAYS tier not available;
        # use solar_max_panels > 0 to confirm the roof has usable solar potential.
        "solar_panels_detected": False,

        # Sections detail (for 3D model)
        "sections":        sections,
        "analysis_notes":  analysis_notes,
        "elapsed_ms":      round((time.time() - t0) * 1000),
        # Debug — pass through so browser Network tab shows solarPotential keys
        "_debug_sp_keys":  solar.get("_debug_sp_keys", []),
    }
