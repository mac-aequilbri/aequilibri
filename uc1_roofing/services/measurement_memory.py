"""Structured measurement and quote memory helpers."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from uc1_roofing.models import (
    MeasurementSnapshot,
    MeasurementUpdate,
    QuoteSnapshot,
)
from uc1_roofing.services.correction_memory import normalize_address_key, to_float


def _decimal(value: Any, default: str = "0") -> Decimal:
    try:
        if value in (None, ""):
            return Decimal(default)
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _json(value: Any, fallback: Any) -> str:
    try:
        return json.dumps(value if value is not None else fallback)
    except (TypeError, ValueError):
        return json.dumps(fallback)


def _list_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def create_measurement_snapshot(
    body: dict[str, Any],
    *,
    quote=None,
    snapshot_type: str | None = None,
) -> tuple[MeasurementSnapshot, MeasurementUpdate]:
    """Create one measurement snapshot plus the user event that produced it."""
    address = str(body.get("address") or "")[:1000]
    lat = to_float(body.get("lat"))
    lng = to_float(body.get("lng"))
    sections = body.get("sections") if isinstance(body.get("sections"), list) else []
    polygon = body.get("polygon") if isinstance(body.get("polygon"), list) else []
    footprint = body.get("footprint") if isinstance(body.get("footprint"), list) else []
    equipment = body.get("equipment") if isinstance(body.get("equipment"), list) else []
    source = str(body.get("source") or "roof_measurement_review")[:80]
    kind = snapshot_type or str(body.get("snapshot_type") or "use_measurements")
    if kind not in {"use_measurements", "quote_form_submit", "quote_generated"}:
        kind = "use_measurements"

    total = _decimal(body.get("total_area_m2"))
    previous = _decimal(body.get("previous_total_area_m2"))
    snapshot = MeasurementSnapshot.objects.create(
        quote=quote,
        snapshot_type=kind,
        source=source,
        address=address,
        address_key=normalize_address_key(address)[:255],
        lat=lat,
        lng=lng,
        total_area_m2=total,
        footprint_area_m2=_decimal(body.get("footprint_area_m2")),
        pitch_deg=_decimal(body.get("pitch_deg") or body.get("avg_pitch_deg"), "0"),
        pitch_factor=_decimal(body.get("pitch_factor"), "1"),
        eave_lm=_decimal(body.get("eave_lm")),
        perimeter_m=_decimal(body.get("perimeter_m")),
        ridge_lm=_decimal(body.get("ridge_lm")),
        valley_lm=_decimal(body.get("valley_lm")),
        hip_lm=_decimal(body.get("hip_lm")),
        rake_lm=_decimal(body.get("rake_lm")),
        storeys=max(1, _int(body.get("storeys"), 1)),
        material=str(body.get("material") or "")[:80],
        roof_colour=str(body.get("roof_colour") or "")[:80],
        section_count=_int(body.get("section_count"), _list_len(sections)),
        outline_vertices=_int(
            body.get("outline_vertices"),
            _list_len(footprint) or _list_len(polygon),
        ),
        equipment_json=_json(equipment, []),
        polygon_json=_json(polygon or footprint, []),
        sections_json=_json(sections, []),
        payload_json=_json(body, {}),
    )
    update_type = str(body.get("update_type") or kind)
    if update_type not in {"use_measurements", "measurement_apply", "quote_form_submit"}:
        update_type = "use_measurements"
    update = MeasurementUpdate.objects.create(
        snapshot=snapshot,
        quote=quote,
        update_type=update_type,
        address=address,
        address_key=snapshot.address_key,
        lat=lat,
        lng=lng,
        previous_total_area_m2=previous,
        new_total_area_m2=total,
        delta_area_m2=total - previous,
        changed_fields_json=_json(body.get("changed_fields"), []),
        payload_json=_json(body, {}),
    )
    return snapshot, update


def attach_measurement_snapshot(snapshot_id: Any, quote) -> MeasurementSnapshot | None:
    try:
        snapshot = MeasurementSnapshot.objects.get(pk=int(snapshot_id))
    except (TypeError, ValueError, MeasurementSnapshot.DoesNotExist):
        return None
    if snapshot.quote_id != quote.pk:
        snapshot.quote = quote
        snapshot.save(update_fields=["quote"])
    snapshot.updates.filter(quote__isnull=True).update(quote=quote)
    return snapshot


def create_quote_snapshot(
    *,
    quote,
    measurement_snapshot=None,
    roof_type: str = "",
    roof_area_m2: Any = 0,
    eave_lm: Any = 0,
    inputs: dict[str, Any] | None = None,
    line_items: list[dict[str, Any]] | None = None,
    pricing_breakdown: dict[str, Any] | None = None,
) -> QuoteSnapshot:
    return QuoteSnapshot.objects.create(
        quote=quote,
        measurement_snapshot=measurement_snapshot,
        address=quote.property_address or "",
        address_key=normalize_address_key(quote.property_address)[:255],
        pricing_mechanism=quote.pricing_mechanism or "",
        pricing_mode=quote.pricing_mode or "",
        package_tier=quote.package_tier or "",
        roof_type=str(roof_type or "")[:30],
        roof_area_m2=_decimal(roof_area_m2),
        eave_lm=_decimal(eave_lm),
        markup_pct=_decimal(quote.markup_pct),
        subtotal_ex_gst=_decimal(quote.subtotal_ex_gst),
        gst_amount=_decimal(quote.gst_total),
        total_inc_gst=_decimal(quote.total_inc_gst),
        inputs_json=_json(inputs or {}, {}),
        line_items_json=_json(line_items or [], []),
        pricing_breakdown_json=_json(pricing_breakdown or {}, {}),
    )
