"""Quality scoring for roof analysis results.

Task 2 from the roof outlining brief: compute a quality signal for every
analysis call so the UI can auto-flag results that need estimator review,
and the eval harness has a numeric success metric.

Pure-python — no scipy / shapely dependencies (so it runs on any Render
deploy without extra build requirements). Convex-hull and polygon-area
helpers are vendored from the eval-harness metrics module to keep the
two in sync.

Public API:
    compute_quality_score(result, geoscape, image_dims, meters_per_pixel)
        → {'signals': {...}, 'quality_score': float, 'needs_review': bool}
"""
from __future__ import annotations

import math
from typing import Any


# ─── Geometry helpers ────────────────────────────────────────────────────────
def polygon_area_pct(pts: list[list[float]]) -> float:
    """Shoelace formula on a polygon expressed in % coordinates."""
    if not pts or len(pts) < 3:
        return 0.0
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def convex_hull_area_pct(pts: list[list[float]]) -> float:
    """Andrew's monotone chain → shoelace."""
    if not pts or len(pts) < 3:
        return 0.0
    sorted_pts = sorted([tuple(p) for p in pts])

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in sorted_pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(sorted_pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    return polygon_area_pct([list(p) for p in hull])


def meters_per_pixel(lat: float, zoom: int) -> float:
    """Web Mercator ground resolution at given latitude & zoom level."""
    try:
        return 156543.03392 * math.cos(math.radians(float(lat))) / (2 ** int(zoom))
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


# ─── Quality scorer ──────────────────────────────────────────────────────────
def compute_quality_score(result: dict, geoscape: dict | None = None,
                          image_dims: tuple[int, int] = (640, 640),
                          meters_per_pixel_val: float | None = None) -> dict:
    """Compute a quality score for a roof-analysis response.

    Parameters
    ----------
    result : dict
        The parsed Claude response — must contain ``roof_outline`` (list
        of [%x, %y] pairs) and ``sections`` (list of dicts with ``polygon``).
    geoscape : dict | None
        Optional Geoscape building data with ``buildingArea`` (m²).
    image_dims : (width, height)
        Pixel dimensions of the satellite image the polygon was drawn on.
    meters_per_pixel_val : float | None
        Optional pre-computed m/px scale. If omitted, area-based signals
        are skipped.

    Returns
    -------
    dict
        ``{signals: {...}, quality_score: float|None, needs_review: bool}``

    Signal contract — every key ending in ``_ok`` is a boolean that
    feeds into the aggregate ``quality_score``. Other keys are
    informational (numeric values used by the eval harness).
    """
    signals: dict[str, Any] = {}

    outline_pct = (result or {}).get('roof_outline') or []
    if not isinstance(outline_pct, list) or len(outline_pct) < 3:
        return {
            'signals': {'degenerate_outline': True},
            'quality_score': 0.0,
            'needs_review': True,
        }

    w, h = image_dims

    # 1. Vertex count
    n_verts = len(outline_pct)
    signals['vertex_count'] = n_verts
    signals['vertex_count_ok'] = 4 <= n_verts <= 20

    # 2. Convexity — polygon_area / convex_hull_area
    poly_area_pct = polygon_area_pct(outline_pct)
    hull_area_pct = convex_hull_area_pct(outline_pct)
    convexity = (poly_area_pct / hull_area_pct) if hull_area_pct > 0 else 0.0
    signals['convexity'] = round(convexity, 3)
    signals['convexity_ok'] = convexity > 0.80

    # 3. Detected area vs Geoscape `buildingArea` (only if both known)
    if meters_per_pixel_val and meters_per_pixel_val > 0:
        # Convert polygon area (in %·%) to m².
        # poly_area_pct is in % coords where the full image is 100×100.
        # Convert to pixels² first, then to m².
        poly_area_px = poly_area_pct * float(w) * float(h) / (100.0 * 100.0)
        detected_area_m2 = poly_area_px * (meters_per_pixel_val ** 2)
        signals['detected_area_m2'] = round(detected_area_m2, 1)

        building_area = None
        if isinstance(geoscape, dict):
            building_area = (geoscape.get('buildingArea')
                             or geoscape.get('building_area')
                             or geoscape.get('area_sqm'))
        if building_area:
            try:
                ratio = detected_area_m2 / float(building_area)
                signals['expected_area_m2'] = float(building_area)
                signals['area_ratio'] = round(ratio, 3)
                signals['area_ratio_ok'] = 0.85 <= ratio <= 1.15
            except (TypeError, ValueError, ZeroDivisionError):
                pass

    # 4. Section count plausibility
    sections = (result or {}).get('sections') or []
    n_sections = len(sections)
    signals['section_count'] = n_sections
    signals['section_count_ok'] = 1 <= n_sections <= 12

    # 5. Section coverage of the outline (sum of section polys ≥ 90%)
    if n_sections > 0 and poly_area_pct > 0:
        total_section_area = sum(
            polygon_area_pct(s.get('polygon') or [])
            for s in sections
        )
        coverage = total_section_area / poly_area_pct if poly_area_pct > 0 else 0
        signals['section_coverage'] = round(coverage, 3)
        signals['section_coverage_ok'] = 0.85 <= coverage <= 1.15  # over-100% means overlap

    # ─── Aggregate ──────────────────────────────────────────────────────
    ok_flags = [v for k, v in signals.items()
                if k.endswith('_ok') and isinstance(v, bool)]
    quality = round(sum(ok_flags) / len(ok_flags), 3) if ok_flags else None

    return {
        'signals':       signals,
        'quality_score': quality,
        'needs_review':  bool(quality is not None and quality < 0.7),
    }


def quality_badge_for_score(score: float | None) -> dict[str, str]:
    """Return a UI-renderable badge spec for a quality score.

    Useful for the estimator UI's "Quality: 0.85 (OK)" pill.
    """
    if score is None:
        return {'label': 'no score', 'colour': '#888', 'background': '#f4f4f4'}
    if score >= 0.85:
        return {'label': f'Quality {score:.2f}', 'colour': '#1b5e20',
                'background': '#e8f5e9'}
    if score >= 0.70:
        return {'label': f'Quality {score:.2f}', 'colour': '#e65100',
                'background': '#fff3e0'}
    return {'label': f'Review {score:.2f}', 'colour': '#b71c1c',
            'background': '#ffebee'}
