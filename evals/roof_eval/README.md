# Roof Outlining Evaluation Harness

Provides a repeatable measurement layer so improvements to the roof
analysis pipeline can be quantified instead of judged subjectively.

## Quick start

```bash
# 1. One-time dependencies
pip install pyyaml requests

# 2. Start the Django dev server (so the runner can call the analysis endpoint)
python manage.py runserver

# 3. In a second terminal — run the eval suite
python -m evals.roof_eval.runner \
    --addresses=evals/roof_eval/addresses.yaml \
    --base-url=http://localhost:8000

# 4. Compute metrics on the run output
python -m evals.roof_eval.metrics \
    --run=evals/roof_eval/runs/<timestamp>

# 5. Print a report comparing against the previous run
python -m evals.roof_eval.report \
    --run=evals/roof_eval/runs/<timestamp> \
    --compare=previous
```

## Components

| File | Purpose |
|---|---|
| `addresses.yaml` | The address set under test. Add 30-50 addresses with category labels and (optional) ground truth. |
| `runner.py` | Calls `/uc1/api/roof-drawing/` for each address; saves response JSON + annotated PNG into `runs/<timestamp>/{id}.json` (+ .png). |
| `metrics.py` | Computes quality metrics (area ratio, vertex count, convexity, section coverage, IoU, ...) for each address in a run. Writes `_metrics_summary.json`. |
| `report.py` | Renders a console comparison table — this run vs a baseline. Flags regressions and false-confidence cases. |
| `runs/` | Output directory. Each run gets a timestamped folder. Safe to delete old runs. |

## Quality metrics reference

| Metric | Target | Notes |
|---|---|---|
| `area_ratio` | 0.85 – 1.15 | `detected_area_m2 / expected_area_m2` (from ground_truth or Geoscape). Outside band suggests an outline that's too tight or includes ground. |
| `vertex_count` | 4 – 20 | Outline with <4 vertices is degenerate; >20 is over-fitted. |
| `convexity` | > 0.80 | `polygon_area / convex_hull_area`. Low = polygon has weird notches or includes non-building shapes. |
| `section_count` | 1 – 12 | Within expected range for residential roofs. |
| `section_coverage` | > 0.90 | Sum of section polygons should cover ≥90% of outline area. |
| `quality_score` | > 0.7 | Composite — fraction of `*_ok` checks passing. < 0.7 → `needs_review = True`. |

## Eval workflow per code change

Recommended workflow when working on tasks from `roof_outlining_claude_code_brief.md`:

```bash
# 1. Baseline (BEFORE any code changes)
python -m evals.roof_eval.runner   --addresses=evals/roof_eval/addresses.yaml
python -m evals.roof_eval.metrics  --run=evals/roof_eval/runs/<baseline-stamp>

# 2. Make code changes (Task N)

# 3. Re-run eval
python -m evals.roof_eval.runner   --addresses=evals/roof_eval/addresses.yaml
python -m evals.roof_eval.metrics  --run=evals/roof_eval/runs/<new-stamp>

# 4. Compare
python -m evals.roof_eval.report --run=evals/roof_eval/runs/<new-stamp> --compare=previous

# 5. Paste the report's delta into the PR description.
```

## Tips

- **Filter to a subset** during dev: `--only=tq_001,tq_002,hist_005`
- **Force a server-side cache bypass** (after Task 1 lands): `--force-refresh`
- **Timeout per address**: `--timeout=120` (default 90s)
- **Inspect a single PNG**: open `runs/<stamp>/<id>.png` — the saved satellite tile with overlay
- **Manifest pinning**: every run copies its `addresses.yaml` into `runs/<stamp>/_manifest.yaml` for reproducibility

## Conventions

- Don't delete addresses from `addresses.yaml` — instead add a `disabled: true` flag in front of any entry that should be skipped (the runner ignores anything without a top-level `id`).
- Don't edit run folders by hand. Treat them as immutable artefacts.
- When adding new metrics in `metrics.py`, the *_ok boolean convention feeds the aggregate `quality_score` — keep that pattern.
