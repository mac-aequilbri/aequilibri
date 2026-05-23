"""Per-address quality metrics for the roof eval harness.

All metrics are pure functions of the saved run JSON — they don't hit any
external service. Run after `runner.py` finishes:

    python -m evals.roof_eval.metrics --run=evals/roof_eval/runs/2026-05-23_14-30-00

Each address's existing JSON is updated in-place with a `metrics` block.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
from typing import Any


def polygon_area_pct(pts: list[list[float]]) -> float:
    """Shoelace area in %·% coordinate space. Use scale to convert to m²."""
    if len(pts) < 3:
        return 0.0
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def convex_hull_area_pct(pts: list[list[float]]) -> float:
    """Andrew's monotone chain convex hull, then shoelace."""
    if len(pts) < 3:
        return 0.0
    sorted_pts = sorted([tuple(p) for p in pts])

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in sorted_pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(sorted_pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    return polygon_area_pct([list(p) for p in hull])


def polygon_iou_pct(poly_a: list[list[float]],
                    poly_b: list[list[float]]) -> float:
    """Intersection-over-Union using a coarse raster grid.

    Pure-python implementation — no shapely dependency. Accuracy is
    limited by the raster resolution (default 1000×1000) but plenty
    good enough for the eval harness's purposes.
    """
    if len(poly_a) < 3 or len(poly_b) < 3:
        return 0.0
    RES = 1000

    def rasterise(poly):
        # Bounding box check then point-in-poly with the even-odd rule.
        # For speed we use a small grid; both polygons share the same grid.
        grid = [[False] * RES for _ in range(RES)]
        for j in range(RES):
            y = (j + 0.5) * 100.0 / RES
            for i in range(RES):
                x = (i + 0.5) * 100.0 / RES
                inside = False
                n = len(poly)
                k = n - 1
                for m in range(n):
                    xm, ym = poly[m]
                    xk, yk = poly[k]
                    if ((ym > y) != (yk > y)) and (
                            x < (xk - xm) * (y - ym) / ((yk - ym) or 1e-9) + xm):
                        inside = not inside
                    k = m
                grid[j][i] = inside
        return grid

    # That 1000×1000 rasterise is slow in pure python (~10s). Drop to
    # 300×300 by default — still useful, much faster.
    RES = 300
    a = rasterise(poly_a)
    b = rasterise(poly_b)
    inter = 0
    union = 0
    for j in range(RES):
        for i in range(RES):
            ai = a[j][i]
            bi = b[j][i]
            if ai and bi:
                inter += 1
            if ai or bi:
                union += 1
    return (inter / union) if union > 0 else 0.0


def compute_metrics(record: dict) -> dict:
    """Compute metrics for one address record (from runner.py output).

    Returns a metrics dict that should be merged into the record's
    'metrics' key.
    """
    metrics: dict[str, Any] = {}

    result = (record or {}).get('result') or {}
    body = result.get('body') if isinstance(result.get('body'), dict) else {}
    if not body:
        metrics['error'] = 'no response body'
        return metrics

    metrics['latency_ms'] = result.get('latency_ms')
    metrics['confidence'] = body.get('confidence')
    metrics['roof_type']  = body.get('roof_type')
    metrics['outline_source'] = body.get('outline_source') or body.get('footprint_source')

    # Outline metrics
    outline = body.get('ai_outline_pct') or body.get('roof_outline') or []
    if isinstance(outline, list) and len(outline) >= 3:
        metrics['vertex_count'] = len(outline)
        metrics['vertex_count_ok'] = 4 <= len(outline) <= 20

        poly_area = polygon_area_pct(outline)
        hull_area = convex_hull_area_pct(outline)
        metrics['convexity'] = round(poly_area / hull_area, 3) if hull_area > 0 else 0.0
        metrics['convexity_ok'] = metrics['convexity'] > 0.80

        # Detected area in m² (using image dimensions + meters_per_px)
        scale = body.get('scale') or {}
        mpp = float(scale.get('meters_per_px') or 0)
        w = float(body.get('width') or 640)
        h = float(body.get('height') or 640)
        if mpp > 0:
            poly_area_px = poly_area * w * h / (100 * 100)
            detected_area_m2 = poly_area_px * mpp * mpp
            metrics['detected_area_m2'] = round(detected_area_m2, 1)

            gt = record.get('ground_truth') or {}
            expected = gt.get('expected_area_m2')
            if expected:
                ratio = detected_area_m2 / float(expected)
                metrics['expected_area_m2'] = expected
                metrics['area_ratio'] = round(ratio, 3)
                metrics['area_ratio_ok'] = 0.85 <= ratio <= 1.15
    else:
        metrics['vertex_count'] = 0
        metrics['degenerate_outline'] = True

    # Section metrics
    sections = body.get('sections') or []
    metrics['section_count'] = len(sections)
    metrics['section_count_ok'] = 1 <= len(sections) <= 12
    gt = record.get('ground_truth') or {}
    if gt.get('expected_section_count'):
        metrics['expected_section_count'] = gt['expected_section_count']
        metrics['section_count_matches_expected'] = (
            len(sections) == gt['expected_section_count'])

    # Section coverage
    if sections and isinstance(outline, list) and len(outline) >= 3:
        section_total = sum(polygon_area_pct(s.get('polygon') or []) for s in sections)
        outline_area  = polygon_area_pct(outline)
        if outline_area > 0:
            cov = section_total / outline_area
            metrics['section_coverage'] = round(cov, 3)
            metrics['section_coverage_ok'] = cov > 0.90

    # Aggregate score — fraction of *_ok flags that are True
    ok_flags = [v for k, v in metrics.items() if k.endswith('_ok')]
    if ok_flags:
        metrics['quality_score'] = round(sum(ok_flags) / len(ok_flags), 3)
        metrics['needs_review'] = metrics['quality_score'] < 0.7

    return metrics


def annotate_run(run_dir: pathlib.Path) -> dict:
    """Walk every per-address json in run_dir, compute metrics, write back."""
    records: list[dict] = []
    for path in sorted(run_dir.glob('*.json')):
        if path.name.startswith('_'):
            continue
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except Exception as exc:
            print(f'[metrics] WARNING skipping unreadable {path.name}: {exc}',
                  file=sys.stderr)
            continue
        record['metrics'] = compute_metrics(record)
        path.write_text(json.dumps(record, indent=2), encoding='utf-8')
        records.append(record)

    # Aggregate across categories + write _metrics_summary.json
    by_category: dict[str, list[dict]] = {}
    for r in records:
        cat = r.get('category', 'unknown')
        by_category.setdefault(cat, []).append(r.get('metrics') or {})

    def mean(items, key):
        vals = [float(i.get(key)) for i in items
                if isinstance(i.get(key), (int, float))]
        return round(sum(vals) / len(vals), 3) if vals else None

    summary = {
        'total': len(records),
        'mean_quality_score': mean([r.get('metrics') or {} for r in records],
                                    'quality_score'),
        'mean_area_ratio':    mean([r.get('metrics') or {} for r in records],
                                    'area_ratio'),
        'mean_convexity':     mean([r.get('metrics') or {} for r in records],
                                    'convexity'),
        'needs_review_count': sum(
            1 for r in records if (r.get('metrics') or {}).get('needs_review')),
        'by_category': {
            cat: {
                'count': len(items),
                'mean_quality_score': mean(items, 'quality_score'),
                'mean_area_ratio':    mean(items, 'area_ratio'),
            }
            for cat, items in by_category.items()
        },
    }
    (run_dir / '_metrics_summary.json').write_text(
        json.dumps(summary, indent=2), encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True,
                        help='Path to a runs/<timestamp>/ directory')
    args = parser.parse_args()
    run_dir = pathlib.Path(args.run)
    if not run_dir.is_dir():
        print(f'[metrics] FATAL: {run_dir} is not a directory', file=sys.stderr)
        sys.exit(2)
    summary = annotate_run(run_dir)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
