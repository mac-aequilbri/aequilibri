"""Append-only correction and ground-truth memory backed by ExecutionLog.

The sprint workflow is still evolving, so these records intentionally live in
the audit log instead of introducing schema churn. This module keeps the
matching and payload-shaping rules out of Django view functions.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from uc1_roofing.models import ExecutionLog


ROOF_CORRECTION_TOOL = "roof_correction"
MANUAL_GROUND_TRUTH_TOOL = "manual_ground_truth"


@dataclass(frozen=True)
class MemoryMatch:
    log: ExecutionLog
    payload: dict[str, Any]
    score: int
    distance_m: float | None


@dataclass(frozen=True)
class CorrectionLearningPrompt:
    text: str
    correction_count: int


def normalize_address_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def distance_m(lat1: float | None, lng1: float | None, lat2: float | None, lng2: float | None) -> float | None:
    if None in (lat1, lng1, lat2, lng2):
        return None
    radius = 6_371_000.0
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    d_phi = math.radians(float(lat2) - float(lat1))
    d_lng = math.radians(float(lng2) - float(lng1))
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lng / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def decode_payload(log: ExecutionLog) -> dict[str, Any] | None:
    try:
        payload = json.loads(log.payload or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def find_best_memory_match(
    *,
    tool_name: str,
    address: str = "",
    lat: float | None = None,
    lng: float | None = None,
    limit: int = 300,
    min_score: int = 500,
) -> MemoryMatch | None:
    address_key = normalize_address_key(address)
    best: MemoryMatch | None = None

    logs = ExecutionLog.objects.filter(
        tool_name=tool_name,
        status="success",
    ).order_by("-created_at")[:limit]

    for log in logs:
        payload = decode_payload(log)
        if not payload:
            continue

        address_score = 0
        candidate_key = normalize_address_key(payload.get("address"))
        if address_key and candidate_key:
            if address_key == candidate_key:
                address_score = 1000
            elif len(address_key) > 10 and (address_key in candidate_key or candidate_key in address_key):
                address_score = 850

        candidate_lat = to_float(payload.get("lat"))
        candidate_lng = to_float(payload.get("lng"))
        proximity = distance_m(lat, lng, candidate_lat, candidate_lng)

        # ── Neighbour-guard ────────────────────────────────────────────────
        # If both addresses are known but don't match, only allow a GPS-only
        # match when the two points are within 10 m of each other (same building,
        # slight geocode variation). This prevents neighbouring properties
        # (typically 20–50 m apart) from inheriting each other's corrections.
        if address_score == 0 and address_key and candidate_key:
            if proximity is None or proximity > 10:
                continue

        score = address_score
        if proximity is not None:
            if proximity <= 8:
                score += 900
            elif proximity <= 30:
                score += 750
            elif proximity <= 75:
                score += 350

        if best is None or score > best.score:
            best = MemoryMatch(log=log, payload=payload, score=score, distance_m=proximity)

    if best is None or best.score < min_score:
        return None
    return best


def match_response(match: MemoryMatch | None, payload_key: str) -> dict[str, Any]:
    if match is None:
        return {"ok": True, "found": False}
    return {
        "ok": True,
        "found": True,
        "id": match.log.id,
        "created_at": match.log.created_at.isoformat(),
        "match_score": match.score,
        "distance_m": round(match.distance_m, 2) if match.distance_m is not None else None,
        payload_key: match.payload,
    }


def save_roof_correction(body: dict[str, Any]) -> int:
    sections = body.get("sections") or []
    footprint = body.get("footprint") or []
    quality = body.get("quality") or {}
    address = str(body.get("address") or "")[:300]
    payload = {
        "address": address,
        "lat": body.get("lat"),
        "lng": body.get("lng"),
        "footprint": footprint,
        "drawing_boundary_pct": body.get("drawing_boundary_pct") or [],
        "sections": sections,
        "quality": quality,
        "footprint_area_m2": body.get("footprint_area_m2"),
        "total_area_m2": body.get("total_area_m2"),
        "perimeter_m": body.get("perimeter_m"),
        "avg_pitch_deg": body.get("avg_pitch_deg"),
        "source": body.get("source") or "ai_roof_drawing",
        "notes": body.get("notes") or "",
    }
    log = ExecutionLog.objects.create(
        tool_name=ROOF_CORRECTION_TOOL,
        payload=json.dumps(payload),
        result=json.dumps({
            "ok": True,
            "address": address,
            "section_count": len(sections) if isinstance(sections, list) else 0,
            "outline_vertices": len(footprint) if isinstance(footprint, list) else 0,
            "quality_level": quality.get("level") if isinstance(quality, dict) else "",
        }),
        status="success",
    )
    return log.id


def save_manual_ground_truth(body: dict[str, Any]) -> int:
    fields = body.get("fields") or {}
    if not isinstance(fields, dict):
        fields = {}
    address = str(body.get("address") or "")[:300]
    payload = {
        "address": address,
        "lat": body.get("lat"),
        "lng": body.get("lng"),
        "sample_id": body.get("sample_id") or "",
        "source": body.get("source") or "peter_manual_sheet",
        "fields": fields,
        "raw_measurements": body.get("raw_measurements") or "",
        "notes": body.get("notes") or "",
    }
    log = ExecutionLog.objects.create(
        tool_name=MANUAL_GROUND_TRUTH_TOOL,
        payload=json.dumps(payload),
        result=json.dumps({
            "ok": True,
            "address": address,
            "field_count": len([value for value in fields.values() if value not in ("", None)]),
        }),
        status="success",
    )
    return log.id


def roof_correction_learning_prompt(limit: int = 8) -> CorrectionLearningPrompt:
    """Build anonymized prompt guidance from recent saved roof corrections.

    Saved corrections are useful as quality-control patterns, but copying raw
    geometry between addresses is unsafe. This summary deliberately excludes
    address names, coordinates, and polygon points.
    """

    try:
        logs = list(
            ExecutionLog.objects.filter(
                tool_name=ROOF_CORRECTION_TOOL,
                status="success",
            ).order_by("-created_at")[: max(1, limit)]
        )
    except Exception:
        return CorrectionLearningPrompt(text="", correction_count=0)

    examples: list[str] = []
    for index, log in enumerate(logs, start=1):
        payload = decode_payload(log)
        if not payload:
            continue

        outline_vertices = _polygon_count(payload.get("footprint")) or _polygon_count(
            payload.get("drawing_boundary_pct")
        )
        sections = payload.get("sections")
        section_count = len(sections) if isinstance(sections, list) else 0
        total_area = _format_number(payload.get("total_area_m2"), suffix=" m2")
        ground_area = _format_number(payload.get("footprint_area_m2"), suffix=" m2")
        avg_pitch = _format_number(_average_pitch(payload), suffix=" deg")
        quality = _quality_level(payload)

        parts = [
            f"example {index}:",
            f"{outline_vertices} outline vertices" if outline_vertices else "",
            f"{section_count} sections" if section_count else "",
            f"total {total_area}" if total_area else "",
            f"ground {ground_area}" if ground_area else "",
            f"avg pitch {avg_pitch}" if avg_pitch else "",
            f"quality {quality}" if quality else "",
        ]
        summary = ", ".join(part for part in parts if part)
        if summary:
            examples.append(f"- {summary}.")

    if not examples:
        return CorrectionLearningPrompt(text="", correction_count=0)

    guidance = [
        "Saved correction memory is available. Use it as QA guidance only; never copy old polygon coordinates or an old roof shape.",
        "Correction patterns to apply on this new image:",
        "- Outline the complete connected roof containing the yellow click/address target.",
        "- Ignore neighboring roofs, detached sheds, carports, trees, shadows, and driveways unless visibly connected to the target roof.",
        "- If an external footprint disagrees with visible roof edges, trust the visible roof. External footprints can miss eaves/extensions or pick the wrong structure.",
        "- Keep every roof section inside the corrected outline; use one section per real roof plane and avoid overlaps.",
        "- Prefer clean rectilinear/hip/gable geometry where image edges support it; do not create tiny sections from shadows, vents, or blur.",
        f"Recent saved corrections checked: {len(examples)}.",
        "Address-free correction examples:",
        *examples,
    ]
    return CorrectionLearningPrompt(text="\n".join(guidance), correction_count=len(examples))


def _polygon_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _format_number(value: Any, *, suffix: str = "") -> str:
    number = to_float(value)
    if number is None or number <= 0:
        return ""
    if abs(number - round(number)) < 0.05:
        return f"{int(round(number))}{suffix}"
    return f"{number:.1f}{suffix}"


def _quality_level(payload: dict[str, Any]) -> str:
    quality = payload.get("quality")
    if not isinstance(quality, dict):
        return ""
    level = str(quality.get("level") or quality.get("status") or "").strip().lower()
    return level[:24]


def _average_pitch(payload: dict[str, Any]) -> float | None:
    explicit = to_float(payload.get("avg_pitch_deg"))
    if explicit is not None:
        return explicit

    sections = payload.get("sections")
    if not isinstance(sections, list):
        return None

    pitches: list[float] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        pitch = to_float(
            section.get("pitch_est")
            or section.get("pitch_deg")
            or section.get("pitch")
        )
        if pitch is not None:
            pitches.append(pitch)
    if not pitches:
        return None
    return sum(pitches) / len(pitches)
