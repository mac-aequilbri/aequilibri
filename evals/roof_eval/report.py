"""Eval comparison report.

Compare a new run against a baseline run (or against the most recent
previous run). Produces a console-friendly summary table + regression
list. Designed to be glance-readable in a PR description.

    python -m evals.roof_eval.report --run=runs/2026-05-23_14-30-00
    python -m evals.roof_eval.report --run=runs/2026-05-23_14-30-00 \
                                      --compare=runs/2026-05-22_10-00-00
    python -m evals.roof_eval.report --run=runs/2026-05-23_14-30-00 --compare=previous
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys


def load_run(run_dir: pathlib.Path) -> tuple[dict, dict]:
    """Returns (summary_dict, per_address_metrics)."""
    summary_path = run_dir / '_metrics_summary.json'
    if not summary_path.exists():
        raise FileNotFoundError(
            f'{summary_path} missing — did you run `python -m evals.roof_eval.metrics --run={run_dir}` ?'
        )
    summary = json.loads(summary_path.read_text(encoding='utf-8'))

    per_addr: dict[str, dict] = {}
    for path in sorted(run_dir.glob('*.json')):
        if path.name.startswith('_'):
            continue
        record = json.loads(path.read_text(encoding='utf-8'))
        per_addr[record['id']] = record
    return summary, per_addr


def find_previous_run(target_dir: pathlib.Path) -> pathlib.Path | None:
    """Return the most recent run folder older than target_dir, or None."""
    parent = target_dir.parent
    if not parent.is_dir():
        return None
    siblings = sorted(
        [p for p in parent.iterdir() if p.is_dir() and p != target_dir],
        key=lambda p: p.name,
    )
    older = [p for p in siblings if p.name < target_dir.name]
    return older[-1] if older else None


def _row(label: str, this_val, prev_val, *, fmt: str = '.3f',
         higher_is_better: bool = True) -> str:
    def f(v):
        if v is None:
            return '   —'
        try:
            return format(v, fmt)
        except (TypeError, ValueError):
            return str(v)
    delta = ''
    if isinstance(this_val, (int, float)) and isinstance(prev_val, (int, float)):
        d = this_val - prev_val
        arrow = '↑' if d > 0 else ('↓' if d < 0 else '·')
        sign  = '+' if d > 0 else ''
        delta = f'   {arrow} {sign}{d:{fmt}}'
        # Direction colour-coding (terminal-safe ASCII only)
        if (d > 0) == higher_is_better:
            delta += '   ✓'
        elif d != 0:
            delta += '   ⚠️'
    return f'  {label:<32} {f(this_val):>8}   {f(prev_val):>8}{delta}'


def render(this_summary: dict, this_addrs: dict,
           prev_summary: dict | None, prev_addrs: dict | None,
           run_path: pathlib.Path, prev_path: pathlib.Path | None) -> None:
    print(f'\n{"═" * 70}')
    print(f'  ROOF EVAL REPORT')
    print(f'  This run:    {run_path}')
    if prev_path:
        print(f'  Baseline:    {prev_path}')
    else:
        print(f'  Baseline:    (none — this is the first run)')
    print(f'{"═" * 70}\n')

    prev_summary = prev_summary or {}
    print(f'  {"Metric":<32} {"THIS":>8}   {"PREV":>8}   Delta')
    print(f'  {"-" * 32} {"-" * 8}   {"-" * 8}   ---------')
    print(_row('total addresses',
               this_summary.get('total'),  prev_summary.get('total'),
               fmt='d', higher_is_better=True))
    print(_row('mean quality_score',
               this_summary.get('mean_quality_score'),
               prev_summary.get('mean_quality_score')))
    print(_row('mean area_ratio (closer to 1.0 = better)',
               this_summary.get('mean_area_ratio'),
               prev_summary.get('mean_area_ratio'), higher_is_better=False))
    print(_row('mean convexity',
               this_summary.get('mean_convexity'),
               prev_summary.get('mean_convexity')))
    print(_row('# needs_review',
               this_summary.get('needs_review_count'),
               prev_summary.get('needs_review_count'),
               fmt='d', higher_is_better=False))

    # Per-category breakdown
    print(f'\n  ─── Per-category quality_score ─────────────────────────────────')
    cats = set((this_summary.get('by_category') or {}).keys())
    cats |= set((prev_summary.get('by_category') or {}).keys())
    for cat in sorted(cats):
        this_cat = (this_summary.get('by_category') or {}).get(cat, {})
        prev_cat = (prev_summary.get('by_category') or {}).get(cat, {})
        print(_row(f'  {cat} (n={this_cat.get("count", 0)})',
                   this_cat.get('mean_quality_score'),
                   prev_cat.get('mean_quality_score')))

    # Regressions: per-address quality drops > 5pp
    if prev_addrs:
        regressions: list[tuple[str, float, float]] = []
        for addr_id, this_rec in this_addrs.items():
            prev_rec = prev_addrs.get(addr_id)
            if not prev_rec:
                continue
            tq = (this_rec.get('metrics') or {}).get('quality_score')
            pq = (prev_rec.get('metrics') or {}).get('quality_score')
            if isinstance(tq, (int, float)) and isinstance(pq, (int, float)):
                if pq - tq > 0.05:
                    regressions.append((addr_id, pq, tq))
        if regressions:
            print(f'\n  ⚠️  Regressions (quality_score dropped > 5pp):')
            for addr_id, pq, tq in regressions:
                print(f'      {addr_id:<14} prev {pq:.2f} → now {tq:.2f}  '
                      f'(Δ {tq - pq:+.2f})')
        else:
            print(f'\n  ✓ No regressions detected.')

    # False-confidence: confidence='high' but quality_score < 0.7
    suspect: list[tuple[str, str | float, float]] = []
    for addr_id, rec in this_addrs.items():
        m = rec.get('metrics') or {}
        if (m.get('confidence') in ('high', 'medium')
            and isinstance(m.get('quality_score'), (int, float))
            and m['quality_score'] < 0.7):
            suspect.append((addr_id, m.get('confidence'), m['quality_score']))
    if suspect:
        print(f'\n  ⚠️  False-confidence (claimed confidence but low quality):')
        for addr_id, conf, q in suspect:
            print(f'      {addr_id:<14} confidence={conf!r}  quality={q:.2f}')

    print(f'\n{"═" * 70}\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True)
    parser.add_argument('--compare', default='',
                        help='Baseline run dir, or "previous" to auto-detect.')
    args = parser.parse_args()

    run_dir = pathlib.Path(args.run)
    if not run_dir.is_dir():
        print(f'[report] FATAL: {run_dir} is not a directory', file=sys.stderr)
        sys.exit(2)

    prev_dir: pathlib.Path | None = None
    if args.compare == 'previous':
        prev_dir = find_previous_run(run_dir)
    elif args.compare:
        prev_dir = pathlib.Path(args.compare)
        if not prev_dir.is_dir():
            print(f'[report] FATAL: --compare {prev_dir} is not a directory',
                  file=sys.stderr)
            sys.exit(2)

    this_summary, this_addrs = load_run(run_dir)
    prev_summary, prev_addrs = (None, None)
    if prev_dir:
        try:
            prev_summary, prev_addrs = load_run(prev_dir)
        except FileNotFoundError as exc:
            print(f'[report] WARNING baseline lacks metrics: {exc}',
                  file=sys.stderr)

    render(this_summary, this_addrs, prev_summary, prev_addrs,
           run_dir, prev_dir)


if __name__ == '__main__':
    main()
