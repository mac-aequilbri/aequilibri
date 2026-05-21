"""Building-footprint lookup service.

Order of preference:
1. Geoscape, when configured and able to identify the selected property.
2. Locally cached Microsoft ML building footprints, lazily importing the
   matching tile when needed.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import urllib.request
from typing import Any

from django.db import connection

from uc1_roofing.geoscape_service import GeoscapeError, lookup_geoscape_building
from uc1_roofing.models import BuildingFootprint


MS_BUILDING_DATASET_LINKS_URL = (
    "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
)
MS_BUILDING_DATASET_ZOOM = 9
_MS_DATASET_INDEX: dict[str, str] | None = None


def lookup_building_footprint(lat: float, lon: float, address: str = "") -> tuple[dict[str, Any], int]:
    """Return the best footprint response payload and HTTP status code."""
    geoscape_message = ""
    try:
        geoscape = lookup_geoscape_building(lat, lon, address=address)
    except GeoscapeError as exc:
        geoscape = None
        geoscape_message = str(exc)
    except Exception as exc:
        geoscape = None
        geoscape_message = f"Geoscape lookup failed: {exc}"

    if geoscape:
        geoscape["count"] = _safe_footprint_count()
        geoscape["imported"] = 0
        return geoscape, 200

    total_before = _safe_footprint_count(default=None)
    if total_before is None:
        return {
            "found": False,
            "count": 0,
            "message": geoscape_message or "Building footprint table not ready yet.",
        }, 200

    imported = 0
    best, best_dist_m = find_nearest_building_footprint(lat, lon)
    if best is None:
        try:
            imported = import_ms_tile_for_point(lat, lon)
        except Exception as exc:
            return {
                "found": False,
                "count": total_before,
                "error": f"Footprint tile lookup failed: {exc}",
            }, 502
        best, best_dist_m = find_nearest_building_footprint(lat, lon)

    total_after = _safe_footprint_count()
    if best is None:
        return {
            "found": False,
            "count": total_after,
            "imported": imported,
            "message": geoscape_message or "No building footprint found within 60 m of the selected point.",
        }, 200

    raw_coords = json.loads(best.geometry)
    leaflet_coords = [[coord[1], coord[0]] for coord in raw_coords]
    return {
        "found": True,
        "area_sqm": round(best.area_sqm),
        "geometry": leaflet_coords,
        "centroid": [best.centroid_lat, best.centroid_lon],
        "distance_m": round(best_dist_m, 1),
        "count": total_after,
        "imported": imported,
        "source": "microsoft",
        "source_label": "Microsoft",
        "source_detail": "microsoft_ml",
        "geoscape_message": geoscape_message,
    }, 200


def quadkey_for_point(lat: float, lon: float, zoom: int = MS_BUILDING_DATASET_ZOOM) -> str:
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    tile_x = int((lon + 180.0) / 360.0 * n)
    tile_y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)

    digits = []
    for i in range(zoom, 0, -1):
        digit = 0
        mask = 1 << (i - 1)
        if tile_x & mask:
            digit += 1
        if tile_y & mask:
            digit += 2
        digits.append(str(digit))
    return "".join(digits)


def import_ms_tile_for_point(lat: float, lon: float) -> int:
    """Lazily cache the Microsoft building-footprint tile for a clicked point."""
    quadkey = quadkey_for_point(lat, lon)
    cache_key = f"{quadkey}:{lat:.3f}:{lon:.3f}"
    if _is_ms_tile_cached(cache_key):
        return 0

    tile_url = _get_ms_dataset_index().get(quadkey)
    if not tile_url:
        _mark_ms_tile_cached(cache_key)
        return 0

    rows: list[BuildingFootprint] = []
    nearby_lat_delta = 300 / 111_000
    nearby_lon_delta = 300 / (111_000 * max(abs(math.cos(math.radians(lat))), 0.001))

    with urllib.request.urlopen(tile_url, timeout=45) as resp:
        with gzip.GzipFile(fileobj=resp) as gz:
            for raw_line in gz:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                geom = obj.get("geometry") if isinstance(obj, dict) else None
                if not geom and isinstance(obj, dict) and obj.get("type") == "Polygon":
                    geom = obj
                if not geom or geom.get("type") != "Polygon":
                    continue

                outer = geom.get("coordinates", [[]])[0]
                if len(outer) < 4:
                    continue

                lons = [coord[0] for coord in outer]
                lats = [coord[1] for coord in outer]
                min_lat, max_lat = min(lats), max(lats)
                min_lon, max_lon = min(lons), max(lons)
                if max_lat < lat - nearby_lat_delta or min_lat > lat + nearby_lat_delta:
                    continue
                if max_lon < lon - nearby_lon_delta or min_lon > lon + nearby_lon_delta:
                    continue

                area = polygon_area_sqm_lonlat(outer)
                if area < 10 or area > 10_000:
                    continue

                rows.append(BuildingFootprint(
                    min_lat=min_lat,
                    max_lat=max_lat,
                    min_lon=min_lon,
                    max_lon=max_lon,
                    centroid_lat=(min_lat + max_lat) / 2,
                    centroid_lon=(min_lon + max_lon) / 2,
                    area_sqm=round(area, 2),
                    geometry=json.dumps(outer),
                ))

    if rows:
        BuildingFootprint.objects.bulk_create(rows, batch_size=2000)
    _mark_ms_tile_cached(cache_key)
    return len(rows)


def find_nearest_building_footprint(lat: float, lon: float) -> tuple[BuildingFootprint | None, float]:
    lat_delta = 50 / 111_000
    lon_delta = 50 / (111_000 * max(abs(math.cos(math.radians(lat))), 0.001))
    candidates = BuildingFootprint.objects.filter(
        min_lat__lte=lat + lat_delta,
        max_lat__gte=lat - lat_delta,
        min_lon__lte=lon + lon_delta,
        max_lon__gte=lon - lon_delta,
    )

    best = None
    best_dist_m = float("inf")
    for footprint in candidates:
        dlat = (footprint.centroid_lat - lat) * 111_000
        dlon = (footprint.centroid_lon - lon) * 111_000 * abs(math.cos(math.radians(lat)))
        dist = math.hypot(dlat, dlon)
        if dist < best_dist_m:
            best_dist_m = dist
            best = footprint

    if best is None or best_dist_m > 60:
        return None, best_dist_m
    return best, best_dist_m


def polygon_area_sqm_lonlat(coords: list[list[float]]) -> float:
    if len(coords) < 3:
        return 0.0
    avg_lat = sum(coord[1] for coord in coords) / len(coords)
    radius = 6_371_000
    lat_m = radius * math.pi / 180
    lon_m = lat_m * math.cos(math.radians(avg_lat))
    area = 0.0
    for i in range(len(coords)):
        j = (i + 1) % len(coords)
        x1, y1 = coords[i][0] * lon_m, coords[i][1] * lat_m
        x2, y2 = coords[j][0] * lon_m, coords[j][1] * lat_m
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _get_ms_dataset_index() -> dict[str, str]:
    global _MS_DATASET_INDEX
    if _MS_DATASET_INDEX is not None:
        return _MS_DATASET_INDEX

    index = {}
    with urllib.request.urlopen(MS_BUILDING_DATASET_LINKS_URL, timeout=30) as resp:
        text = io.TextIOWrapper(resp, encoding="utf-8")
        for row in csv.DictReader(text):
            row = {str(key).strip(): str(value).strip() for key, value in row.items()}
            if row.get("Location", "").lower() != "australia":
                continue
            quadkey = row.get("QuadKey", "")
            url = row.get("Url", "")
            if quadkey and url:
                index[quadkey] = url
    _MS_DATASET_INDEX = index
    return index


def _safe_footprint_count(default: int | None = 0) -> int | None:
    try:
        return BuildingFootprint.objects.count()
    except Exception:
        return default


def _ensure_ms_tile_cache_table() -> None:
    with connection.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS uc1_roofing_footprinttilecache (
                quadkey varchar(32) NOT NULL PRIMARY KEY,
                imported_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def _is_ms_tile_cached(quadkey: str) -> bool:
    _ensure_ms_tile_cache_table()
    with connection.cursor() as cur:
        cur.execute("SELECT 1 FROM uc1_roofing_footprinttilecache WHERE quadkey = %s LIMIT 1", [quadkey])
        return cur.fetchone() is not None


def _mark_ms_tile_cached(quadkey: str) -> None:
    _ensure_ms_tile_cache_table()
    with connection.cursor() as cur:
        cur.execute("INSERT OR IGNORE INTO uc1_roofing_footprinttilecache (quadkey) VALUES (%s)", [quadkey])

