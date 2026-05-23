"""Roof outlining evaluation harness.

Purpose: provide a repeatable measurement layer so improvements to the
roof analysis pipeline can be quantified instead of judged subjectively.

Usage:
    python -m evals.roof_eval.runner --addresses=evals/roof_eval/addresses.yaml
    python -m evals.roof_eval.report --run=runs/2026-05-23-14-30 --compare=previous

See `roof_outlining_claude_code_brief.md` Task 0 for full spec.
"""
