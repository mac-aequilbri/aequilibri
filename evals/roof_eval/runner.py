"""Eval runner — calls the roof analysis endpoint for each address in the
YAML set and captures the result + annotated PNG for offline inspection.

CLI:
    python -m evals.roof_eval.runner \
        --addresses=evals/roof_eval/addresses.yaml \
        --base-url=http://localhost:8000 \
        --out-dir=evals/roof_eval/runs

Each run produces a timestamped folder under runs/ with:
    {addr_id}.json   — full API response + metrics
    {addr_id}.png    — annotated satellite image (when returned)
    _summary.json    — per-run summary stats + per-address index
    _manifest.yaml   — copy of addresses.yaml used for the run

The runner is stateless: re-running on the same set creates a new
timestamped folder without touching previous runs.
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import datetime as _dt
import json
import os
import pathlib
import sys
import time
from typing import Any

# Don't make PyYAML a hard import — fall back to a tiny tolerant parser.
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


@dataclasses.dataclass
class AddressEntry:
    id: str
    address: str
    lat: float
    lng: float
    category: str = ''
    notes: str = ''
    ground_truth: dict | None = None


def load_addresses(path: str | pathlib.Path) -> list[AddressEntry]:
    if not HAS_YAML:
        raise RuntimeError(
            'PyYAML is required to read addresses.yaml. '
            'pip install pyyaml'
        )
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    raw_list = data.get('addresses') or []
    entries: list[AddressEntry] = []
    for raw in raw_list:
        try:
            entries.append(AddressEntry(
                id=str(raw['id']),
                address=str(raw['address']),
                lat=float(raw['lat']),
                lng=float(raw['lng']),
                category=str(raw.get('category', '')),
                notes=str(raw.get('notes', '')),
                ground_truth=raw.get('ground_truth') or None,
            ))
        except (KeyError, TypeError, ValueError) as exc:
            print(f'[runner] WARNING: skipping malformed entry: {raw!r} ({exc})',
                  file=sys.stderr)
    return entries


def call_analysis(base_url: str, entry: AddressEntry,
                  force_refresh: bool = False, timeout_s: float = 90.0) -> dict:
    """POST to /uc1/api/roof-drawing/ and return the parsed JSON."""
    if not HAS_REQUESTS:
        raise RuntimeError('requests is required. pip install requests')
    url = base_url.rstrip('/') + '/uc1/api/roof-drawing/'
    if force_refresh:
        url += '?force_refresh=1'
    payload = {
        'address': entry.address,
        'lat': entry.lat,
        'lng': entry.lng,
        'zoom': 20,
        'max_zoom': 21,
    }
    t0 = time.monotonic()
    try:
        r = requests.post(url, json=payload, timeout=timeout_s)
        latency_ms = (time.monotonic() - t0) * 1000
        return {
            'ok': r.ok,
            'status': r.status_code,
            'latency_ms': round(latency_ms, 1),
            'body': r.json() if r.headers.get('content-type', '').startswith('application/json') else r.text,
        }
    except Exception as exc:
        return {
            'ok': False,
            'status': 0,
            'latency_ms': round((time.monotonic() - t0) * 1000, 1),
            'body': {'error': repr(exc)},
        }


def save_run(out_dir: pathlib.Path, entry: AddressEntry, result: dict) -> dict:
    """Persist one address's run to disk. Returns a small dict for the summary."""
    out_path = out_dir / f'{entry.id}.json'
    record = {
        'id': entry.id,
        'address': entry.address,
        'category': entry.category,
        'lat': entry.lat,
        'lng': entry.lng,
        'ground_truth': entry.ground_truth,
        'result': result,
    }
    out_path.write_text(json.dumps(record, indent=2), encoding='utf-8')

    # Also extract + save the annotated PNG if returned (key: image_b64)
    body = result.get('body') if isinstance(result.get('body'), dict) else None
    if body and isinstance(body.get('image_b64'), str):
        try:
            png_bytes = base64.b64decode(body['image_b64'])
            (out_dir / f'{entry.id}.png').write_bytes(png_bytes)
        except Exception as exc:
            print(f'[runner] WARNING: failed to save PNG for {entry.id}: {exc}',
                  file=sys.stderr)

    return {
        'id': entry.id,
        'category': entry.category,
        'ok': result.get('ok'),
        'status': result.get('status'),
        'latency_ms': result.get('latency_ms'),
    }


def main():
    parser = argparse.ArgumentParser(description='Roof analysis eval runner')
    parser.add_argument('--addresses', default='evals/roof_eval/addresses.yaml')
    parser.add_argument('--base-url',  default='http://localhost:8000')
    parser.add_argument('--out-dir',   default='evals/roof_eval/runs')
    parser.add_argument('--force-refresh', action='store_true',
                        help='Bypass server-side cache (requires Task 1).')
    parser.add_argument('--only', default='',
                        help='Comma-separated address IDs to include (subset).')
    parser.add_argument('--timeout', type=float, default=90.0)
    args = parser.parse_args()

    entries = load_addresses(args.addresses)
    if args.only:
        wanted = {s.strip() for s in args.only.split(',') if s.strip()}
        entries = [e for e in entries if e.id in wanted]
    if not entries:
        print('[runner] No addresses to run. Check --addresses path / --only filter.',
              file=sys.stderr)
        sys.exit(1)

    run_stamp = _dt.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    out_dir = pathlib.Path(args.out_dir) / run_stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    # Manifest = copy of the addresses used (for diff / reproducibility)
    with open(args.addresses, encoding='utf-8') as src, \
         open(out_dir / '_manifest.yaml', 'w', encoding='utf-8') as dst:
        dst.write(src.read())

    print(f'[runner] Running {len(entries)} addresses against {args.base_url}')
    print(f'[runner] Output → {out_dir}')

    summary: list[dict] = []
    for i, entry in enumerate(entries, start=1):
        print(f'  [{i:>2}/{len(entries)}] {entry.id}  {entry.address[:50]}...', end='', flush=True)
        result = call_analysis(args.base_url, entry,
                               force_refresh=args.force_refresh,
                               timeout_s=args.timeout)
        rec = save_run(out_dir, entry, result)
        summary.append(rec)
        status_marker = '✓' if result.get('ok') else '✗'
        print(f'  {status_marker}  {result.get("latency_ms"):>6.0f} ms')

    # Write summary
    summary_obj = {
        'run': run_stamp,
        'base_url': args.base_url,
        'addresses_count': len(entries),
        'addresses': summary,
        'ok_count':   sum(1 for r in summary if r['ok']),
        'fail_count': sum(1 for r in summary if not r['ok']),
        'mean_latency_ms': round(
            sum(r['latency_ms'] for r in summary if r['latency_ms']) /
            max(1, len(summary)), 1),
    }
    (out_dir / '_summary.json').write_text(
        json.dumps(summary_obj, indent=2), encoding='utf-8')
    print(f'[runner] Done. ok={summary_obj["ok_count"]} '
          f'fail={summary_obj["fail_count"]} '
          f'mean_latency={summary_obj["mean_latency_ms"]} ms')
    print(f'[runner] Next: python -m evals.roof_eval.report --run={out_dir}')


if __name__ == '__main__':
    main()
