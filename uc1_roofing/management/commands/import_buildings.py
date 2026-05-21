"""
Management command: import_buildings
------------------------------------
Downloads Microsoft Global ML Building Footprints for Queensland
from the current (2025/2026) dataset hosted as per-quadkey compressed tiles.

Usage:
    python manage.py import_buildings

How it works:
  1. Fetches the dataset index (dataset-links.csv, ~500 KB)
  2. Filters to Australia tiles whose bounding boxes overlap QLD
  3. Downloads each tile (~400–700 tiles, ~300 MB total compressed)
  4. Parses GeoJSON-Lines format and inserts QLD buildings into SQLite

Expected outcome: ~2–4 million QLD building polygons in 5–20 minutes.
"""

import csv
import gzip
import io
import json
import math
import os
import threading
import queue
import urllib.request
import urllib.error
from django.core.management.base import BaseCommand
from django.db import connection

# ── Dataset index (updated Feb 2026) ─────────────────────────────────────────
DATASET_LINKS_URL = (
    "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
)

# ── QLD bounding box ──────────────────────────────────────────────────────────
QLD_MIN_LAT, QLD_MAX_LAT = -29.5, -9.9
QLD_MIN_LON, QLD_MAX_LON = 137.9, 153.6

BATCH_SIZE    = 5_000
MIN_AREA      = 10      # m²
MAX_AREA      = 10_000  # m²
PARALLEL_DL   = 6       # simultaneous tile downloads
REQUEST_TO    = 45      # seconds per tile download


# ── Quadkey helpers ───────────────────────────────────────────────────────────

def quadkey_to_bbox(quadkey: str):
    """
    Convert a Bing Maps quadkey string to a (min_lon, min_lat, max_lon, max_lat)
    bounding box in WGS84 degrees.
    """
    tile_x = tile_y = 0
    zoom = len(quadkey)
    for i, ch in enumerate(quadkey):
        mask = 1 << (zoom - 1 - i)
        if ch in ('1', '3'):
            tile_x |= mask
        if ch in ('2', '3'):
            tile_y |= mask

    n = 2 ** zoom
    west  = tile_x / n * 360.0 - 180.0
    east  = (tile_x + 1) / n * 360.0 - 180.0

    def tile_lat(ty):
        rad = math.atan(math.sinh(math.pi * (1 - 2 * ty / n)))
        return math.degrees(rad)

    north = tile_lat(tile_y)
    south = tile_lat(tile_y + 1)
    return west, south, east, north


def overlaps_qld(west, south, east, north) -> bool:
    return (
        west  <= QLD_MAX_LON and east  >= QLD_MIN_LON and
        south <= QLD_MAX_LAT and north >= QLD_MIN_LAT
    )


# ── Geometry helpers ──────────────────────────────────────────────────────────

def polygon_area_sqm(coords):
    n = len(coords)
    if n < 3:
        return 0.0
    avg_lat = sum(c[1] for c in coords) / n
    R = 6_371_000
    lat_m = R * math.pi / 180
    lon_m = lat_m * math.cos(math.radians(avg_lat))
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        x1, y1 = coords[i][0] * lon_m, coords[i][1] * lat_m
        x2, y2 = coords[j][0] * lon_m, coords[j][1] * lat_m
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


# ── Django management command ─────────────────────────────────────────────────

class Command(BaseCommand):
    help = "Download & import Microsoft ML Building Footprints (QLD tiles) into SQLite"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear", action="store_true",
            help="Delete all existing imported footprints before importing",
        )
        parser.add_argument(
            "--workers", type=int, default=PARALLEL_DL,
            help=f"Parallel download threads (default {PARALLEL_DL})",
        )

    def handle(self, *args, **options):
        # ── optional clear ────────────────────────────────────────────────────
        if options["clear"]:
            from uc1_roofing.models import BuildingFootprint
            n = BuildingFootprint.objects.count()
            BuildingFootprint.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Cleared {n:,} existing footprints."))

        self._ensure_table()

        # ── fetch index ───────────────────────────────────────────────────────
        self.stdout.write(f"Fetching dataset index from Microsoft…")
        tile_urls = self._get_qld_tile_urls()
        self.stdout.write(
            f"Found {len(tile_urls)} tile files covering QLD  "
            f"(~{len(tile_urls) * 0.5:.0f}–{len(tile_urls) * 0.7:.0f} MB compressed)\n"
        )

        if not tile_urls:
            self.stderr.write(self.style.ERROR(
                "No QLD tiles found — the dataset index may have changed. "
                "Check: " + DATASET_LINKS_URL
            ))
            return

        # ── download + import tiles (parallel) ────────────────────────────────
        self.stdout.write(
            f"Downloading and importing tiles with {options['workers']} parallel workers…\n"
            f"(This may take 5–20 minutes — grab a coffee ☕)\n"
        )

        total = self._import_parallel(tile_urls, workers=options["workers"])

        self.stdout.write(
            self.style.SUCCESS(f"\n✅  Done! Imported {total:,} QLD building footprints.")
        )
        self.stdout.write(
            "Restart the Django dev server and hard-refresh the quote page to use ML detection."
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _ensure_table(self):
        with connection.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "uc1_roofing_buildingfootprint" (
                    "id"           integer NOT NULL PRIMARY KEY AUTOINCREMENT,
                    "min_lat"      real    NOT NULL,
                    "max_lat"      real    NOT NULL,
                    "min_lon"      real    NOT NULL,
                    "max_lon"      real    NOT NULL,
                    "centroid_lat" real    NOT NULL,
                    "centroid_lon" real    NOT NULL,
                    "area_sqm"     real    NOT NULL,
                    "geometry"     text    NOT NULL
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS "bf_lat_idx"
                ON "uc1_roofing_buildingfootprint" ("min_lat", "max_lat")
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS "bf_lon_idx"
                ON "uc1_roofing_buildingfootprint" ("min_lon", "max_lon")
            """)
        self.stdout.write("  Database table ready.")

    def _get_qld_tile_urls(self):
        """Fetch dataset-links.csv and return URLs for tiles overlapping QLD."""
        try:
            req = urllib.request.urlopen(DATASET_LINKS_URL, timeout=30)
            text = io.TextIOWrapper(req, encoding="utf-8")
            reader = csv.DictReader(text)
            rows = list(reader)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Failed to fetch index: {exc}"))
            return []

        # Normalise column names (strip whitespace)
        urls = []
        for row in rows:
            row = {k.strip(): v.strip() for k, v in row.items()}
            location = row.get("Location", row.get("location", ""))
            if location.lower() != "australia":
                continue

            url = row.get("Url", row.get("url", ""))
            if not url:
                continue

            quadkey = row.get("QuadKey", row.get("quadkey", ""))
            if quadkey:
                try:
                    bbox = quadkey_to_bbox(quadkey)
                    if not overlaps_qld(*bbox):
                        continue
                except Exception:
                    pass   # can't filter → include it

            urls.append(url)

        return urls

    def _import_parallel(self, urls, workers):
        """Download and import tiles using a thread pool."""
        url_queue  = queue.Queue()
        result_q   = queue.Queue()
        lock       = threading.Lock()
        total      = [0]
        done       = [0]
        n_tiles    = len(urls)

        for u in urls:
            url_queue.put(u)

        insert_sql = (
            "INSERT INTO uc1_roofing_buildingfootprint "
            "(min_lat, max_lat, min_lon, max_lon, centroid_lat, centroid_lon, area_sqm, geometry) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )

        def worker():
            while True:
                try:
                    url = url_queue.get_nowait()
                except queue.Empty:
                    return

                rows = self._parse_tile(url)

                if rows:
                    with lock:
                        with connection.cursor() as cur:
                            cur.executemany(insert_sql, rows)
                        total[0] += len(rows)

                with lock:
                    done[0] += 1
                    if done[0] % 10 == 0 or done[0] == n_tiles:
                        pct = done[0] * 100 // n_tiles
                        self.stdout.write(
                            f"\r  [{done[0]:4d}/{n_tiles}] {pct:3d}%  "
                            f"{total[0]:,} QLD buildings imported",
                            ending=""
                        )
                        self.stdout.flush()

                url_queue.task_done()

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.stdout.write("")   # newline after \r progress
        return total[0]

    def _parse_tile(self, url):
        """
        Download a single .csv.gz tile and return rows ready for bulk insert.
        Each row: (min_lat, max_lat, min_lon, max_lon, centroid_lat, centroid_lon, area_sqm, geometry_json)
        """
        rows = []
        try:
            with urllib.request.urlopen(url, timeout=REQUEST_TO) as resp:
                with gzip.GzipFile(fileobj=resp) as f:
                    for raw_line in f:
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # Support both Feature format and bare geometry+properties
                        if "geometry" in obj:
                            geom = obj["geometry"]
                        elif "type" in obj and obj["type"] == "Polygon":
                            geom = obj
                        else:
                            continue

                        if not geom or geom.get("type") != "Polygon":
                            continue

                        outer = geom["coordinates"][0]
                        if len(outer) < 4:
                            continue

                        lons = [c[0] for c in outer]
                        lats = [c[1] for c in outer]
                        min_lat, max_lat = min(lats), max(lats)
                        min_lon, max_lon = min(lons), max(lons)

                        # Quick QLD filter
                        if max_lat < QLD_MIN_LAT or min_lat > QLD_MAX_LAT:
                            continue
                        if max_lon < QLD_MIN_LON or min_lon > QLD_MAX_LON:
                            continue

                        area = polygon_area_sqm(outer)
                        if area < MIN_AREA or area > MAX_AREA:
                            continue

                        rows.append((
                            min_lat, max_lat, min_lon, max_lon,
                            (min_lat + max_lat) / 2,
                            (min_lon + max_lon) / 2,
                            round(area, 2),
                            json.dumps(outer),   # [[lon, lat], ...]
                        ))
        except Exception:
            pass   # network blip / bad tile — skip silently

        return rows
