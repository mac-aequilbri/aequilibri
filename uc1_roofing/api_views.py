"""Thin API controllers for UC1 Roofing.

Business logic belongs in service modules; this file should mostly handle HTTP
input/output, validation, and status codes.
"""

from __future__ import annotations

import json
import base64
import urllib.request

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from uc1_roofing.models import PITCH_FACTORS, Quote, RoofLidarAnalysis, VendorMaterialPrice
from uc1_roofing.services.correction_memory import (
    MANUAL_GROUND_TRUTH_TOOL,
    ROOF_CORRECTION_TOOL,
    find_best_memory_match,
    match_response,
    save_manual_ground_truth,
    save_roof_correction,
    to_float,
)
from uc1_roofing.services.footprints import lookup_building_footprint


def area_preview(request):
    """Return adjusted roof area for the quote form preview."""
    try:
        flat_area = float(request.GET.get("flat_area", 0))
        pitch_type = request.GET.get("pitch_type", "standard")
        waste_factor = float(request.GET.get("waste_factor", 10))
        pitch_factor = PITCH_FACTORS.get(pitch_type, 1.0)
        adjusted = round(flat_area * pitch_factor * (1 + waste_factor / 100), 2)
        return JsonResponse({"adjusted_area": adjusted, "pitch_factor": pitch_factor})
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=400)


def building_lookup(request):
    """Return the best Geoscape/Microsoft building footprint near a point."""
    try:
        lat = float(request.GET.get("lat", 0))
        lon = float(request.GET.get("lon", 0))
    except (ValueError, TypeError):
        return JsonResponse({"found": False, "error": "Invalid coordinates"}, status=400)

    address = (request.GET.get("address") or "").strip()
    payload, status = lookup_building_footprint(lat, lon, address=address)
    return JsonResponse(payload, status=status)


def api_vendor_prices(request):
    """Return active vendor prices for the price monitor table."""
    items = (
        VendorMaterialPrice.objects
        .filter(is_available=True, vendor__is_active=True)
        .select_related("vendor")
        .order_by("vendor__name", "material")
    )
    return JsonResponse({"prices": [
        {
            "vendor": item.vendor.name,
            "material": item.get_material_display(),
            "item_code": item.item_code,
            "price": str(item.unit_price_ex_gst),
            "unit": item.unit,
            "lead_days": item.lead_days,
            "last_verified": item.last_verified.strftime("%d %b %Y") if item.last_verified else None,
            "source_url": item.price_source_url or None,
        }
        for item in items
    ]})


@csrf_exempt
def lidar_analyze(request):
    """Run LiDAR/roof analysis and optionally persist it for a quote."""
    from uc1_roofing.lidar_service import full_roof_analysis

    try:
        body = _json_body(request)
        lat = float(body["lat"])
        lng = float(body["lng"])
        polygon = body.get("polygon", [])
        storeys = int(body.get("storeys", 1))
        solar_panels = bool(body.get("solar_panels", False))
        solar_hw = bool(body.get("solar_hw", False))
        quote_id = body.get("quote_id")

        if len(polygon) < 3:
            return JsonResponse({"error": "polygon must have at least 3 points"}, status=400)

        result = full_roof_analysis(
            lat=lat,
            lng=lng,
            polygon_coords=polygon,
            storeys=storeys,
            solar_panels=solar_panels,
            solar_hw=solar_hw,
        )
        if quote_id:
            _persist_lidar_analysis(quote_id, result)
        return JsonResponse(result)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@csrf_exempt
def roof_correction_save(request):
    """Persist or retrieve a verified AI roof drawing correction."""
    if request.method == "GET":
        match = find_best_memory_match(
            tool_name=ROOF_CORRECTION_TOOL,
            address=str(request.GET.get("address") or "")[:300],
            lat=to_float(request.GET.get("lat")),
            lng=to_float(request.GET.get("lng")),
        )
        return JsonResponse(match_response(match, "correction"))

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Unsupported method"}, status=405)

    try:
        log_id = save_roof_correction(_json_body(request))
        return JsonResponse({"ok": True, "id": log_id})
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


@csrf_exempt
def manual_ground_truth_save(request):
    """Persist or retrieve manual/Peter measurement evidence."""
    if request.method == "GET":
        match = find_best_memory_match(
            tool_name=MANUAL_GROUND_TRUTH_TOOL,
            address=str(request.GET.get("address") or "")[:300],
            lat=to_float(request.GET.get("lat")),
            lng=to_float(request.GET.get("lng")),
        )
        return JsonResponse(match_response(match, "ground_truth"))

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Unsupported method"}, status=405)

    try:
        log_id = save_manual_ground_truth(_json_body(request))
        return JsonResponse({"ok": True, "id": log_id})
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


def _json_body(request) -> dict:
    return json.loads(request.body or b"{}")


def _persist_lidar_analysis(quote_id, result: dict) -> None:
    try:
        quote = Quote.objects.get(pk=quote_id)
    except Quote.DoesNotExist:
        return

    scaffolding = result["scaffolding"]
    RoofLidarAnalysis.objects.update_or_create(
        quote=quote,
        defaults={
            "perimeter_m": result["perimeter_m"],
            "guttering_linear_m": result["guttering_linear_m"],
            "ridge_height_m": result.get("ridge_height_m"),
            "eave_height_m": result.get("eave_height_m"),
            "height_range_m": result.get("height_range_m"),
            "scaffolding_required": scaffolding["required"],
            "scaffolding_linear_m": scaffolding.get("estimated_linear_m", 0),
            "scaffolding_risk_level": scaffolding.get("risk_level", "low"),
            "scaffolding_reason": scaffolding.get("reason", ""),
            "structure_count": result.get("structure_count", 1),
            "structures_json": json.dumps(result.get("structures", [])),
            "solar_panels": result["solar_panels"],
            "solar_hw": result["solar_hw"],
            "lidar_coverage": result["lidar_coverage"],
            "data_source": result["data_source"],
            "analysis_notes": json.dumps(result["analysis_notes"]),
            "elapsed_ms": result["elapsed_ms"],
        },
    )


# ── Feature Detection via Claude Vision ───────────────────────────────────────

@csrf_exempt
def detect_roof_features(request):
    """
    Fetch a satellite image of the roof via Google Static Maps API and
    send it to Claude Vision to detect solar panels, solar hot water,
    and other rooftop features.
    POST { lat, lng }
    Returns { solar_panels, solar_hw, other_features, notes, demo_mode }
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body)
        lat  = float(body["lat"])
        lng  = float(body["lng"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    # ── 1. Fetch satellite image from Google Static Maps ──────────────────
    api_key = getattr(settings, "GOOGLE_MAPS_API_KEY", "")
    img_b64  = None
    if api_key:
        static_url = (
            f"https://maps.googleapis.com/maps/api/staticmap"
            f"?center={lat},{lng}&zoom=20&size=640x640"
            f"&maptype=satellite&key={api_key}"
        )
        try:
            req = urllib.request.Request(static_url, headers={"User-Agent": "aequilibri/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                img_b64 = base64.b64encode(resp.read()).decode()
        except Exception:
            img_b64 = None

    # ── 2. Call Claude Vision ─────────────────────────────────────────────
    from core.claude_client import call_claude_vision, call_claude

    if img_b64:
        system = (
            "You are an expert roof inspector analysing an aerial satellite photograph "
            "of an Australian residential or commercial property. "
            "Your task is to visually identify features present on the roof. "
            "Respond ONLY with a valid JSON object — no markdown, no extra text."
        )
        prompt = (
            "Examine this satellite rooftop image carefully. Identify if the following "
            "are visibly present:\n"
            "1. Solar PV panels (dark rectangular panels in arrays)\n"
            "2. Solar hot water system (flat plate collectors or evacuated tube collectors, "
            "often with a cylindrical tank)\n"
            "3. Any other notable rooftop features (e.g. skylights, roof vents, "
            "air conditioning units, antennas, heritage features)\n\n"
            "Respond with ONLY this JSON:\n"
            '{"solar_panels": true/false, "solar_panels_confidence": "high/medium/low", '
            '"solar_hw": true/false, "solar_hw_confidence": "high/medium/low", '
            '"other_features": ["list of strings or empty array"], '
            '"notes": "one sentence summary"}'
        )
        result = call_claude_vision(system, prompt, img_b64, media_type="image/png", max_tokens=512)
    else:
        # No image available — fall back to text-only Claude with a note
        result = {"content": '{"solar_panels":false,"solar_panels_confidence":"low","solar_hw":false,"solar_hw_confidence":"low","other_features":[],"notes":"Satellite image unavailable — manual inspection required."}', "demo_mode": True}

    # ── 3. Parse and return ───────────────────────────────────────────────
    try:
        raw = result["content"].strip()
        # Strip any accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
    except (json.JSONDecodeError, IndexError):
        data = {
            "solar_panels": False,
            "solar_panels_confidence": "low",
            "solar_hw": False,
            "solar_hw_confidence": "low",
            "other_features": [],
            "notes": "Could not parse AI response.",
        }

    data["demo_mode"] = result.get("demo_mode", False)
    return JsonResponse(data)

