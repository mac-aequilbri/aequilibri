#!/usr/bin/env python
r"""Migrate selected saved roof corrections from local SQLite to Render.

The roof drawing "Save Correction" feature stores corrections as successful
ExecutionLog rows with tool_name="roof_correction". This script copies the
latest correction payload for the four client demo addresses to the Render
instance through the existing /uc1/api/roof-correction/ endpoint.

Default mode is a dry run. Use --execute to write to Render.

Examples:
    python scripts/migrate_roof_corrections_to_render.py ^
        --source-db C:\Users\antonim3\Documents\aequilibri_poc\db.sqlite3

    python scripts/migrate_roof_corrections_to_render.py ^
        --source-db C:\Users\antonim3\Documents\aequilibri_poc\db.sqlite3 ^
        --render-url https://aequilibri.onrender.com ^
        --replace-remote ^
        --execute
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ENDPOINT_PATH = "/uc1/api/roof-correction/"

TARGETS = [
    {
        "key": "sandhill",
        "label": "8 Sandhill Road - Rita Island",
        "aliases": ["8 sandhill", "sandhill road", "sandhill rd"],
    },
    {
        "key": "ahern",
        "label": "11 Ahern Street - Ayr",
        "aliases": ["11 ahern", "ahern street", "ahern st"],
    },
    {
        "key": "condron",
        "label": "11 Condron Place - Ayr",
        "aliases": ["11 condron", "condron place", "condron pl"],
    },
    {
        "key": "dalrymple",
        "label": "238 Dalrymple Service Road - Vincent",
        "aliases": ["238 dalrymple", "dalrymple service", "dalrymple rd", "dalrymple road"],
    },
]


@dataclass
class CorrectionRecord:
    target_key: str
    target_label: str
    log_id: int | None
    created_at: str
    payload: dict[str, Any]


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def selected_targets(raw: str) -> list[dict[str, Any]]:
    if not raw or raw.lower() == "all":
        return TARGETS
    wanted = {normalize(part) for part in raw.split(",") if part.strip()}
    targets = [target for target in TARGETS if normalize(target["key"]) in wanted]
    missing = wanted - {normalize(target["key"]) for target in targets}
    if missing:
        raise SystemExit(f"Unknown target(s): {', '.join(sorted(missing))}")
    return targets


def target_matches_address(target: dict[str, Any], address: str) -> bool:
    haystack = normalize(address)
    return any(normalize(alias) in haystack for alias in target["aliases"])


def correction_summary(payload: dict[str, Any]) -> str:
    footprint = payload.get("footprint") or []
    sections = payload.get("sections") or []
    return (
        f"area={payload.get('total_area_m2')} m2, "
        f"footprint_vertices={len(footprint) if isinstance(footprint, list) else 0}, "
        f"sections={len(sections) if isinstance(sections, list) else 0}, "
        f"lat={payload.get('lat')}, lng={payload.get('lng')}"
    )


def validate_payload(record: CorrectionRecord) -> list[str]:
    payload = record.payload
    issues: list[str] = []
    if not payload.get("address"):
        issues.append("missing address")
    try:
        if float(payload.get("total_area_m2") or 0) <= 0:
            issues.append("missing/zero total_area_m2")
    except (TypeError, ValueError):
        issues.append("invalid total_area_m2")
    footprint = payload.get("footprint")
    if not isinstance(footprint, list) or len(footprint) < 3:
        issues.append("footprint has fewer than 3 vertices")
    if payload.get("lat") in (None, "") or payload.get("lng") in (None, ""):
        issues.append("missing lat/lng")
    return issues


def load_corrections_from_sqlite(db_path: Path, targets: list[dict[str, Any]]) -> list[CorrectionRecord]:
    if not db_path.exists():
        raise SystemExit(f"Source database not found: {db_path}")

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            select id, created_at, payload
            from uc1_roofing_executionlog
            where tool_name = 'roof_correction'
              and status = 'success'
            order by datetime(created_at) desc, id desc
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise SystemExit(f"Could not read ExecutionLog rows from {db_path}: {exc}") from exc
    finally:
        con.close()

    found: dict[str, CorrectionRecord] = {}
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        address = str(payload.get("address") or "")
        for target in targets:
            if target["key"] in found:
                continue
            if target_matches_address(target, address):
                found[target["key"]] = CorrectionRecord(
                    target_key=target["key"],
                    target_label=target["label"],
                    log_id=int(row["id"]),
                    created_at=str(row["created_at"] or ""),
                    payload=payload,
                )

    missing = [target["label"] for target in targets if target["key"] not in found]
    if missing:
        raise SystemExit("Missing local correction(s): " + "; ".join(missing))
    return [found[target["key"]] for target in targets]


def load_corrections_from_json(json_path: Path, targets: list[dict[str, Any]]) -> list[CorrectionRecord]:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read input JSON {json_path}: {exc}") from exc

    if isinstance(data, dict) and "corrections" in data:
        data = data["corrections"]
    if not isinstance(data, list):
        raise SystemExit("Input JSON must be a list, or an object with a 'corrections' list.")

    records: list[CorrectionRecord] = []
    for item in data:
        payload = item.get("payload") if isinstance(item, dict) else None
        if not isinstance(payload, dict):
            continue
        target_key = str(item.get("target_key") or "")
        target = next((t for t in targets if t["key"] == target_key), None)
        if target is None:
            target = next((t for t in targets if target_matches_address(t, str(payload.get("address") or ""))), None)
        if target is None:
            continue
        records.append(
            CorrectionRecord(
                target_key=target["key"],
                target_label=target["label"],
                log_id=item.get("log_id"),
                created_at=str(item.get("created_at") or ""),
                payload=payload,
            )
        )

    by_key = {record.target_key: record for record in records}
    missing = [target["label"] for target in targets if target["key"] not in by_key]
    if missing:
        raise SystemExit("Input JSON is missing correction(s): " + "; ".join(missing))
    return [by_key[target["key"]] for target in targets]


def export_records(records: list[CorrectionRecord], output_path: Path) -> None:
    output = {
        "corrections": [
            {
                "target_key": record.target_key,
                "target_label": record.target_label,
                "source_log_id": record.log_id,
                "source_created_at": record.created_at,
                "payload": record.payload,
            }
            for record in records
        ]
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def endpoint(base_url: str) -> str:
    return base_url.rstrip("/") + ENDPOINT_PATH


def http_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: int = 30,
    token: str = "",
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed with HTTP {exc.code}: {raw}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"{method} {url} failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{method} {url} did not return JSON") from exc


def correction_query(payload: dict[str, Any]) -> str:
    params = {
        "address": payload.get("address") or "",
        "lat": payload.get("lat") or "",
        "lng": payload.get("lng") or "",
    }
    return urllib.parse.urlencode(params)


def compare_payloads(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    expected_area = float(expected.get("total_area_m2") or 0)
    actual_area = float(actual.get("total_area_m2") or 0)
    if abs(expected_area - actual_area) > 0.05:
        issues.append(f"area mismatch: expected {expected_area}, got {actual_area}")

    expected_footprint = expected.get("footprint") or []
    actual_footprint = actual.get("footprint") or []
    if len(expected_footprint) != len(actual_footprint):
        issues.append(
            f"footprint vertex mismatch: expected {len(expected_footprint)}, got {len(actual_footprint)}"
        )

    expected_sections = expected.get("sections") or []
    actual_sections = actual.get("sections") or []
    if len(expected_sections) != len(actual_sections):
        issues.append(f"section count mismatch: expected {len(expected_sections)}, got {len(actual_sections)}")
    return issues


def migrate_record(
    record: CorrectionRecord,
    *,
    base_url: str,
    execute: bool,
    replace_remote: bool,
    timeout: int,
    token: str,
) -> bool:
    remote_endpoint = endpoint(base_url)
    payload = record.payload
    print(f"\n[{record.target_label}]")
    print(f"  local log: {record.log_id} ({record.created_at})")
    print(f"  address: {payload.get('address')}")
    print(f"  payload: {correction_summary(payload)}")

    issues = validate_payload(record)
    if issues:
        print("  ERROR: invalid local payload: " + "; ".join(issues))
        return False

    if not execute:
        print("  DRY RUN: would migrate this correction to " + remote_endpoint)
        return True

    query = correction_query(payload)
    if replace_remote:
        delete_url = f"{remote_endpoint}?{query}"
        deleted = http_json("DELETE", delete_url, timeout=timeout, token=token)
        if not deleted.get("ok"):
            print(f"  ERROR: remote delete failed: {deleted}")
            return False
        print(f"  remote replace: deleted {deleted.get('deleted', 0)} existing matching correction(s)")

    created = http_json("POST", remote_endpoint, body=payload, timeout=timeout, token=token)
    if not created.get("ok"):
        print(f"  ERROR: remote POST failed: {created}")
        return False
    print(f"  remote created log id: {created.get('id')}")

    verified = http_json("GET", f"{remote_endpoint}?{query}", timeout=timeout, token=token)
    if not verified.get("ok") or not verified.get("found"):
        print(f"  ERROR: remote verification failed: {verified}")
        return False

    remote_payload = verified.get("correction") or {}
    compare_issues = compare_payloads(payload, remote_payload)
    if compare_issues:
        print("  ERROR: verification mismatch: " + "; ".join(compare_issues))
        return False

    print(
        "  verified: "
        f"remote id={verified.get('id')}, "
        f"match_score={verified.get('match_score')}, "
        f"distance_m={verified.get('distance_m')}"
    )
    return True


def parse_args(argv: list[str]) -> argparse.Namespace:
    default_source_db = os.environ.get("LOCAL_ROOF_CORRECTIONS_DB") or "db.sqlite3"
    default_render_url = os.environ.get("RENDER_BASE_URL") or "https://aequilibri.onrender.com"

    parser = argparse.ArgumentParser(
        description="Migrate the four local roof correction payloads to Render."
    )
    parser.add_argument("--source-db", default=default_source_db, help="Local SQLite database path.")
    parser.add_argument("--input-json", default="", help="Read correction payloads from an exported JSON file.")
    parser.add_argument("--export-json", default="", help="Write the selected correction payloads to JSON.")
    parser.add_argument("--render-url", default=default_render_url, help="Render app base URL.")
    parser.add_argument("--targets", default="all", help="Comma-separated target keys, or 'all'.")
    parser.add_argument("--execute", action="store_true", help="Actually write corrections to Render.")
    parser.add_argument(
        "--replace-remote",
        action="store_true",
        help="Before POSTing, delete matching remote corrections for each address.",
    )
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    parser.add_argument(
        "--api-token",
        default=os.environ.get("ROOF_CORRECTION_MIGRATION_TOKEN", ""),
        help="Optional bearer token header if production later protects the endpoint.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    targets = selected_targets(args.targets)

    if args.input_json:
        records = load_corrections_from_json(Path(args.input_json), targets)
    else:
        records = load_corrections_from_sqlite(Path(args.source_db), targets)

    if args.export_json:
        export_records(records, Path(args.export_json))
        print(f"Exported {len(records)} correction payload(s) to {args.export_json}")

    print(f"Loaded {len(records)} correction payload(s).")
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY RUN'}")
    if args.execute and not args.render_url.startswith("https://"):
        print("WARNING: Render URL is not HTTPS: " + args.render_url)

    ok = True
    for record in records:
        migrated = migrate_record(
            record,
            base_url=args.render_url,
            execute=args.execute,
            replace_remote=args.replace_remote,
            timeout=args.timeout,
            token=args.api_token,
        )
        ok = ok and migrated

    print("\nResult: " + ("OK" if ok else "FAILED"))
    if not args.execute:
        print("Dry run only. Re-run with --execute to write to Render.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
