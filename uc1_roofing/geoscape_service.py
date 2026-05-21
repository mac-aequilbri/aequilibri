"""Geoscape Buildings/Addresses API helpers for roof footprint lookup."""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from django.conf import settings

from uc1_roofing.services.paid_api_cache import (
    LONG_TTL_SECONDS,
    NEGATIVE_TTL_SECONDS,
    clone_jsonable,
    get_cached,
    normalized_address,
    rounded_point,
    set_cached,
)


GEOSCAPE_API_BASE = "https://api.psma.com.au/v2"
GEOSCAPE_ADDRESSES_BASE = "https://api.psma.com.au/v2/addresses"
GEOSCAPE_ADDITIONAL_PROPERTIES = "height,roof,solar"


class GeoscapeError(Exception):
    """Raised when Geoscape is configured but a request cannot be completed."""


def geoscape_configured() -> bool:
    return bool(getattr(settings, "GEOSCAPE_CONSUMER_KEY", ""))


def lookup_geoscape_building(lat: float, lon: float, address: str = "") -> dict[str, Any] | None:
    """
    Return a normalized building footprint from Geoscape or None.

    The Addresses API is used first when an address is available because address
    geocodes often land at a frontage or parcel centroid rather than on the roof.
    """
    if not geoscape_configured():
        return None

    address = (address or "").strip()
    cache_payload = {
        **rounded_point(lat, lon),
        "address": normalized_address(address),
        "properties": GEOSCAPE_ADDITIONAL_PROPERTIES,
    }
    cached = get_cached("geoscape_building_lookup", cache_payload)
    if cached is not None:
        if cached.get("found"):
            result = clone_jsonable(cached["result"])
            result["cache_hit"] = True
            return result
        return None

    result = None
    if _looks_like_address(address):
        address_feature = _geocode_address(address)
        address_id = ((address_feature or {}).get("properties") or {}).get("addressId")
        if address_id:
            data = _api_get(
                f"{GEOSCAPE_API_BASE}/buildings/findByIdentifier",
                {
                    "addressId": address_id,
                    "additionalProperties": GEOSCAPE_ADDITIONAL_PROPERTIES,
                    "limit": 12,
                    "offset": 0,
                },
            )
            best = _best_building_feature(data, lat, lon, "geoscape_address")
            if best:
                best["address_id"] = address_id
                best["address_match"] = _address_match_summary(address_feature)
                result = best

    if result is None:
        data = _api_get(
            f"{GEOSCAPE_API_BASE}/buildings/findByPoint",
            {
                "latitude": f"{lat:.6f}",
                "longitude": f"{lon:.6f}",
                "radius": 60,
                "additionalProperties": GEOSCAPE_ADDITIONAL_PROPERTIES,
                "limit": 12,
                "offset": 0,
            },
        )
        result = _best_building_feature(data, lat, lon, "geoscape_point")

    if result:
        result["cache_hit"] = False
        set_cached("geoscape_building_lookup", cache_payload, {
            "found": True,
            "result": clone_jsonable(result),
        }, LONG_TTL_SECONDS)
        return result

    set_cached("geoscape_building_lookup", cache_payload, {"found": False}, NEGATIVE_TTL_SECONDS)
    return None


def _api_get(url: str, params: dict[str, Any]) -> dict[str, Any]:
    consumer_key = getattr(settings, "GEOSCAPE_CONSUMER_KEY", "")
    if not consumer_key:
        raise GeoscapeError("Geoscape consumer key is not configured")

    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    req = urllib.request.Request(
        f"{url}?{query}",
        headers={
            "Authorization": consumer_key,
            "Accept": "application/geo+json, application/json",
            "User-Agent": "aequilibri-roofing/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=14) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        message = _extract_error_message(body) or f"HTTP {exc.code}"
        raise GeoscapeError(f"Geoscape request failed: {message}") from exc
    except Exception as exc:
        raise GeoscapeError(f"Geoscape request failed: {exc}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GeoscapeError("Geoscape returned a non-JSON response") from exc


def _geocode_address(address: str) -> dict[str, Any] | None:
    data = _api_get(
        f"{GEOSCAPE_ADDRESSES_BASE}/geocoder",
        {
            "address": address,
            "matchType": "address",
            "maxResults": 3,
            "additionalProperties": "buildings",
        },
    )
    features = data.get("features") or []
    if not isinstance(features, list):
        return None
    scored = []
    for feat in features:
        props = feat.get("properties") or {}
        if not props.get("addressId"):
            continue
        score = _num(feat.get("matchScore"), _num(props.get("matchScore"), 0))
        scored.append((score, feat))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _best_building_feature(data: dict[str, Any], lat: float, lon: float, source_detail: str) -> dict[str, Any] | None:
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        return None

    candidates = []
    for feature in features:
        cand = _normalize_building_feature(feature, lat, lon, source_detail)
        if cand:
            candidates.append(cand)
    if not candidates:
        return None

    def sort_key(cand: dict[str, Any]):
        contains_penalty = 0 if cand.get("contains_query_point") else 1000
        primary_penalty = 0 if str(cand.get("primary_building", "")).lower() == "yes" else 20
        distance = _num(cand.get("distance_m"), 9999)
        return contains_penalty + primary_penalty + distance

    candidates.sort(key=sort_key)
    return candidates[0]


def _normalize_building_feature(
    feature: dict[str, Any], lat: float, lon: float, source_detail: str
) -> dict[str, Any] | None:
    if not isinstance(feature, dict):
        return None
    props = feature.get("properties") or {}
    rings = _outer_rings(feature.get("geometry") or {})
    if not rings:
        return None

    ring_choices = []
    for ring in rings:
        area = _polygon_area_sqm_latlng(ring)
        if area < 4:
            continue
        centroid = _centroid(ring)
        ring_choices.append({
            "geometry": ring,
            "area": area,
            "centroid": centroid,
            "contains": _point_in_poly_latlng(lat, lon, ring),
            "distance": _haversine_m(lat, lon, centroid[0], centroid[1]),
        })
    if not ring_choices:
        return None

    ring_choices.sort(key=lambda r: (0 if r["contains"] else 1, r["distance"], -r["area"]))
    chosen = ring_choices[0]
    prop_area = _num(props.get("buildingArea"), _num(props.get("area"), chosen["area"]))
    area = chosen["area"] if len(ring_choices) > 1 else prop_area
    centroid_lat = _num(props.get("centroidLatitude"), chosen["centroid"][0])
    centroid_lon = _num(props.get("centroidLongitude"), chosen["centroid"][1])

    roof = props.get("roof") or props.get("heightsAndRoofs") or {}
    height = props.get("height") or {}
    solar = props.get("solar") or props.get("solarIndicator") or {}

    return {
        "found": True,
        "source": "geoscape",
        "source_label": "Geoscape",
        "source_detail": source_detail,
        "area_sqm": round(area, 1),
        "geometry": chosen["geometry"],
        "centroid": [round(centroid_lat, 7), round(centroid_lon, 7)],
        "distance_m": round(_haversine_m(lat, lon, centroid_lat, centroid_lon), 1),
        "contains_query_point": bool(chosen["contains"]),
        "building_pid": props.get("buildingPid") or props.get("buildingId") or "",
        "primary_building": props.get("primaryBuildingFlag") or "",
        "building_use": props.get("buildingUse") or "",
        "capture_resolution": props.get("captureResolution") or "",
        "capture_method": props.get("captureMethod") or "",
        "roof_shape": roof.get("roofShape") or roof.get("roofType") or "",
        "roof_slope": roof.get("roofSlope"),
        "roof_material": roof.get("primaryRoofMaterial") or "",
        "roof_colour": roof.get("roofColour") or "",
        "eave_height_m": height.get("eaveHeight") or roof.get("eaveHeight"),
        "roof_height_m": height.get("roofHeight") or roof.get("roofHeight"),
        "solar_panel": solar.get("solarPanel") if "solarPanel" in solar else solar.get("solarFlag"),
        "solar_flag": solar.get("solarFlag") or "",
        "solar_area_m2": solar.get("solarArea"),
        "solar_daily_estimated_power_kwh": solar.get("dailyEstimatedPower"),
        "solar_capture_date": solar.get("dateLastCaptured") or solar.get("dateSolarPanelReviewed") or "",
        "solar_capture_resolution": solar.get("captureResolution") or "",
        "solar_capture_method": solar.get("captureMethod") or "",
    }


def _outer_rings(geometry: dict[str, Any]) -> list[list[list[float]]]:
    gtype = str(geometry.get("type") or "").lower()
    coords = geometry.get("coordinates") or []
    raw_rings = []
    if gtype == "polygon":
        if coords:
            raw_rings.append(coords[0])
    elif gtype == "multipolygon":
        for poly in coords:
            if poly:
                raw_rings.append(poly[0])

    rings = []
    for ring in raw_rings:
        clean = []
        for pos in ring or []:
            if not isinstance(pos, (list, tuple)) or len(pos) < 2:
                continue
            try:
                lon = float(pos[0])
                lat = float(pos[1])
            except (TypeError, ValueError):
                continue
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                clean.append([round(lat, 7), round(lon, 7)])
        if len(clean) > 3 and clean[0] == clean[-1]:
            clean = clean[:-1]
        if len(clean) >= 3:
            rings.append(clean)
    return rings


def _point_in_poly_latlng(lat: float, lon: float, poly: list[list[float]]) -> bool:
    inside = False
    j = len(poly) - 1
    for i, point in enumerate(poly):
        yi, xi = point[0], point[1]
        yj, xj = poly[j][0], poly[j][1]
        intersects = ((xi > lon) != (xj > lon)) and (
            lat < (yj - yi) * (lon - xi) / ((xj - xi) or 1e-12) + yi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _polygon_area_sqm_latlng(poly: list[list[float]]) -> float:
    if len(poly) < 3:
        return 0.0
    mean_lat = sum(p[0] for p in poly) / len(poly)
    cos_lat = max(abs(math.cos(math.radians(mean_lat))), 0.001)
    pts = [(p[1] * 111_320 * cos_lat, p[0] * 111_320) for p in poly]
    area = 0.0
    for i, p in enumerate(pts):
        q = pts[(i + 1) % len(pts)]
        area += p[0] * q[1] - q[0] * p[1]
    return abs(area) / 2


def _centroid(poly: list[list[float]]) -> list[float]:
    return [
        sum(p[0] for p in poly) / len(poly),
        sum(p[1] for p in poly) / len(poly),
    ]


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _looks_like_address(value: str) -> bool:
    return len(value) >= 8 and any(ch.isalpha() for ch in value)


def _address_match_summary(feature: dict[str, Any] | None) -> dict[str, Any]:
    if not feature:
        return {}
    props = feature.get("properties") or {}
    return {
        "formatted_address": props.get("formattedAddress") or "",
        "match_type": feature.get("matchType") or props.get("matchType") or "",
        "match_score": feature.get("matchScore") or props.get("matchScore") or None,
        "geo_feature": props.get("geoFeature") or "",
    }


def _extract_error_message(body: str) -> str:
    if not body:
        return ""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return body[:180]
    if isinstance(data, dict):
        fault = data.get("fault") or {}
        if isinstance(fault, dict) and fault.get("faultstring"):
            return str(fault["faultstring"])
        for key in ("message", "error", "error_description"):
            if data.get(key):
                return str(data[key])
    return body[:180]
