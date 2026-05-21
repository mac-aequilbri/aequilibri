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
    Fetch a satellite image + Google Street View image and send both to
    Claude Vision for a richer rooftop analysis.

    POST { lat, lng }

    Returns {
        solar_panels, solar_panels_confidence,
        solar_hw, solar_hw_confidence,
        roof_style, roof_style_confidence,
        roof_material, roof_material_confidence,
        storeys, condition,
        other_features, notes,
        views_used,   # e.g. ["aerial", "streetview"]
        demo_mode
    }
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body)
        lat = float(body["lat"])
        lng = float(body["lng"])
        # Optional: caller may pass the already-cropped roof drawing image so we
        # use the tight satellite crop instead of a fresh wide-angle fetch.
        roof_image_b64   = body.get("roof_image_b64") or ""
        roof_media_type  = body.get("roof_media_type") or "image/png"
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    google_key = getattr(settings, "GOOGLE_MAPS_API_KEY", "")
    images = []  # list of {"b64": str, "media_type": str, "label": str}

    # ── 1. Aerial image — prefer the cropped roof drawing image when available ─
    # The roof drawing is already tightly cropped to the selected building, giving
    # Claude a much clearer view of rooftop features (especially solar panels) than
    # a fresh wide-angle zoom-20 static map fetch.
    if roof_image_b64:
        images.append({
            "b64": roof_image_b64,
            "media_type": roof_media_type,
            "label": "aerial",
        })
    elif google_key:
        satellite_url = (
            f"https://maps.googleapis.com/maps/api/staticmap"
            f"?center={lat},{lng}&zoom=20&size=640x640"
            f"&maptype=satellite&key={google_key}"
        )
        try:
            req = urllib.request.Request(satellite_url, headers={"User-Agent": "aequilibri/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                images.append({
                    "b64": base64.b64encode(resp.read()).decode(),
                    "media_type": "image/png",
                    "label": "aerial",
                })
        except Exception:
            pass

    # ── 2. Check Street View availability ────────────────────────────────
    sv_available = False
    if google_key:
        meta_url = (
            f"https://maps.googleapis.com/maps/api/streetview/metadata"
            f"?location={lat},{lng}&radius=100&source=outdoor&key={google_key}"
        )
        try:
            req = urllib.request.Request(meta_url, headers={"User-Agent": "aequilibri/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                sv_available = json.loads(resp.read()).get("status") == "OK"
        except Exception:
            sv_available = False

    # ── 3. Fetch 4-direction Street Views (N / E / S / W) ─────────────────
    # Each heading = camera points that direction → reveals the opposite facade.
    # pitch=12 gives a slight upward angle to show roof edges clearly.
    _SV_HEADINGS = [
        (0,   "NORTH"),   # camera→N: reveals south-facing facade + south roof slope
        (90,  "EAST"),    # camera→E: reveals west-facing facade + west roof slope
        (180, "SOUTH"),   # camera→S: reveals north-facing (street) facade + north slope
        (270, "WEST"),    # camera→W: reveals east-facing facade + east roof slope
    ]
    if sv_available and google_key:
        for heading, direction in _SV_HEADINGS:
            sv_url = (
                f"https://maps.googleapis.com/maps/api/streetview"
                f"?size=640x480&location={lat},{lng}"
                f"&heading={heading}&fov=90&pitch=12&source=outdoor&key={google_key}"
            )
            try:
                req = urllib.request.Request(sv_url, headers={"User-Agent": "aequilibri/1.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    images.append({
                        "b64": base64.b64encode(resp.read()).decode(),
                        "media_type": "image/jpeg",
                        "label": f"sv_{direction.lower()}",
                    })
            except Exception:
                pass

    # ── 4. Build prompt and call Claude Vision ────────────────────────────
    from core.claude_client import call_claude_vision, call_claude_vision_multi

    has_aerial = any(i["label"] == "aerial" for i in images)
    sv_labels  = [i["label"] for i in images if i["label"].startswith("sv_")]

    _SV_DESCRIPTIONS = {
        "sv_north": "camera faces North — reveals south-facing facade and south roof slope",
        "sv_east":  "camera faces East  — reveals west-facing facade and west roof slope",
        "sv_south": "camera faces South — reveals north-facing (street) facade and north slope",
        "sv_west":  "camera faces West  — reveals east-facing facade and east roof slope",
    }

    _BASE_JSON = (
        '"solar_panels": true/false, '
        '"solar_panels_confidence": "high/medium/low", '
        '"solar_hw": true/false, '
        '"solar_hw_confidence": "high/medium/low", '
        '"roof_style": "gable/hip/flat/skillion/mansard/unknown", '
        '"roof_style_confidence": "high/medium/low", '
        '"roof_material": "terracotta_tiles/concrete_tiles/metal_colorbond/asphalt/slate/unknown", '
        '"roof_material_confidence": "high/medium/low", '
        '"pitch_deg": 20, '
        '"storeys": 1, '
        '"eave_height_m": 3.0, '
        '"condition": "good/fair/poor/unknown", '
        '"other_features": ["list or empty array"], '
        '"notes": "one sentence summary"'
    )

    if not images:
        result = {
            "content": (
                '{"solar_panels":false,"solar_panels_confidence":"low",'
                '"solar_hw":false,"solar_hw_confidence":"low",'
                '"roof_style":"unknown","roof_style_confidence":"low",'
                '"roof_material":"unknown","roof_material_confidence":"low",'
                '"pitch_deg":null,"storeys":null,"eave_height_m":null,'
                '"condition":"unknown","other_features":[],'
                '"notes":"No imagery available — manual inspection required."}'
            ),
            "demo_mode": True,
            "views_used": [],
        }

    # Whether the aerial is a tight roof crop or a wider view affects how
    # confidently we can detect features.
    aerial_is_cropped = bool(roof_image_b64)
    aerial_desc = (
        "a tightly cropped satellite image of the selected roof"
        if aerial_is_cropped else
        "a top-down satellite image of the property"
    )

    _SOLAR_PANEL_GUIDANCE = (
        "═══ SOLAR PANEL DETECTION — examine carefully before answering ═══\n"
        "Solar PV panels look like ONE OR MORE of the following on the aerial image:\n"
        "  • Dark blue/black rectangular cells in a neat rectangular grid pattern\n"
        "  • A uniform dark rectangle or strip on the roof, noticeably darker than "
        "the rest of the roof surface (metal, tile, or terracotta)\n"
        "  • May appear slightly raised and cast tiny edge shadows\n"
        "  • On corrugated iron roofs: a rectangular block of flat dark material "
        "contrasting with the ribbed texture — very obvious\n"
        "  • In Street View: dark rectangular panels visible on a sloped roof face\n"
        "  • Even a single row of 4+ panels counts as detected\n\n"
        "BIAS RULE: If there is ANY area that could plausibly be solar panels, "
        "set solar_panels=true and solar_panels_confidence='medium'. "
        "A missed detection is a missed solar upsell — far worse than a false positive "
        "that a site inspector can quickly dismiss. When in doubt, say true."
    )

    if sv_labels:
        # Multi-image: aerial + up to 4 directional Street Views
        n_sv = len(sv_labels)
        system = (
            f"You are an expert Australian roof inspector. You will be given "
            f"{1 + n_sv} images of the same property: {aerial_desc} "
            f"and {n_sv} street-level views from different compass directions. "
            "Respond ONLY with a valid JSON object — no markdown, no extra text."
        )
        # Build per-image bullet list
        bullets = [
            f"• IMAGE 1 — AERIAL ({aerial_desc}): "
            "Look carefully for solar PV panels (dark rectangular grid arrays, "
            "often darker than the roof), solar hot water (flat collector + cylindrical tank), "
            "AC units, skylights, pools."
        ]
        for idx, lbl in enumerate(sv_labels, 2):
            desc = _SV_DESCRIPTIONS.get(lbl, "street-level view")
            bullets.append(f"• IMAGE {idx} — STREET VIEW {lbl.split('_')[1].upper()} ({desc}).")

        prompt = (
            "You have the following images of the same Australian property:\n\n"
            + "\n".join(bullets)
            + f"\n\n{_SOLAR_PANEL_GUIDANCE}\n\n"
            "Using ALL images together:\n"
            "• Solar panels / hot water → aerial (IMAGE 1) — look carefully at the roof surface\n"
            "• Roof style, material → street views (judge from multiple angles)\n"
            "• Pitch in degrees → measure slope steepness from street views "
            "(flat <5°, low 5–15°, medium 15–25°, steep 25–35°, very steep >35°)\n"
            "• Storey count, eave height → any street view "
            "(1-storey ≈ 3 m eave, 2-storey ≈ 5–6 m, 3-storey ≈ 8–9 m)\n"
            "• Roof condition → street views (moss, cracked tiles, sag, rust)\n\n"
            "Respond with ONLY this JSON (no markdown fences):\n"
            "{" + _BASE_JSON + "}"
        )
        result = call_claude_vision_multi(system, prompt, images, max_tokens=800,
                                          model="claude-opus-4-7")
        result["views_used"] = ["aerial"] + sv_labels

    else:
        # Satellite only — best-effort from aerial alone
        system = (
            f"You are an expert roof inspector analysing {aerial_desc} "
            "of an Australian residential or commercial property. "
            "Respond ONLY with a valid JSON object — no markdown, no extra text."
        )
        prompt = (
            f"Examine this {aerial_desc} carefully.\n\n"
            f"{_SOLAR_PANEL_GUIDANCE}\n\n"
            "Also identify: solar hot water (flat collector + cylindrical tank), "
            "AC units, skylights, pools. "
            "Roof style, material and condition are difficult to determine from aerial "
            "only — set those to 'unknown' unless clearly visible. "
            "Set pitch_deg and eave_height_m to null.\n\n"
            "Respond with ONLY this JSON (no markdown fences):\n"
            "{" + _BASE_JSON + "}"
        )
        img = images[0]
        result = call_claude_vision(
            system, prompt, img["b64"], media_type=img["media_type"], max_tokens=800
        )
        result["views_used"] = ["aerial"]

    # ── 4. Parse JSON response ────────────────────────────────────────────
    try:
        raw = result["content"].strip()
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
            "roof_style": "unknown",
            "roof_style_confidence": "low",
            "roof_material": "unknown",
            "roof_material_confidence": "low",
            "storeys": None,
            "condition": "unknown",
            "other_features": [],
            "notes": "Could not parse AI response.",
        }

    data["demo_mode"]  = result.get("demo_mode", False)
    data["views_used"] = result.get("views_used", [])

    # ── Surface silent Claude API errors ───────────────────────────────────
    # call_claude_vision*() catches exceptions and embeds the error into the
    # JSON `notes` field, returning solar_panels=false.  Without this surfacing
    # an API error (invalid model, rate-limit, network blip) looks IDENTICAL
    # to "Claude said no panels here".  Expose it so the UI can distinguish.
    notes = str(data.get("notes") or "")
    error_markers = ("Claude error:", "Could not parse AI response",
                     "No imagery available")
    if any(m in notes for m in error_markers):
        data["detection_error"] = notes
        print(f"[detect_roof_features] LIKELY API ERROR — lat={lat} lng={lng}  notes={notes!r}")
    return JsonResponse(data)

