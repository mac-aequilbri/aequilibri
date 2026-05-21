"""UC1 Roofing Estimator — Views"""
import json
import math
import base64
import re
import csv
import gzip
import io
import time
import urllib.request
import urllib.parse
from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings

from .models import (Quote, QuoteItem, Contact, RateCard, RoofPolygon,
                     ExecutionLog, BuildingFootprint,
                     Vendor, VendorMaterialPrice, PurchaseOrder, PurchaseOrderItem,
                     PriceCheckLog, RoofLidarAnalysis,
                     PITCH_FACTORS, PITCH_CHOICES, MATERIAL_CHOICES,
                     GutteringRate, SolarPartner, SolarReferral, FinanceProvider,
                     StormEvent, StormLead, RoofConditionReport)
from .forms import QuoteForm, ContactForm, RateCardForm
from .geoscape_service import GeoscapeError, lookup_geoscape_building
from .services.correction_memory import (roof_correction_learning_prompt,
                                          extract_suburb, suburb_section_pattern)
from .services.paid_api_cache import SHORT_TTL_SECONDS, get_cached, set_cached


MS_BUILDING_DATASET_LINKS_URL = (
    "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
)
MS_BUILDING_DATASET_ZOOM = 9
_MS_DATASET_INDEX = None


# ─── Roof Inspector ───────────────────────────────────────────────────────────

def roof_inspector(request):
    return render(request, 'uc1_roofing/roof_inspector.html')


# ─── Dashboard ────────────────────────────────────────────────────────────────

def dashboard(request):
    quotes    = Quote.objects.select_related('contact').all()[:10]
    total_q   = Quote.objects.count()
    accepted  = Quote.objects.filter(status='accepted').count()
    draft     = Quote.objects.filter(status='draft').count()
    rate_cards = RateCard.objects.filter(is_active=True).count()
    context = {
        'quotes': quotes, 'total_q': total_q,
        'accepted': accepted, 'draft': draft,
        'rate_cards': rate_cards,
    }
    return render(request, 'uc1_roofing/dashboard.html', context)


# ─── Quote List ───────────────────────────────────────────────────────────────

def quote_list(request):
    status_filter = request.GET.get('status', '')
    qs = Quote.objects.select_related('contact').all()
    if status_filter:
        qs = qs.filter(status=status_filter)
    return render(request, 'uc1_roofing/quote_list.html', {
        'quotes': qs,
        'status_filter': status_filter,
        'status_choices': Quote._meta.get_field('status').choices,
    })


# ─── Quote Create ─────────────────────────────────────────────────────────────

def quote_create(request):
    rate_cards = RateCard.objects.filter(is_active=True)
    pitch_factors_json = json.dumps(PITCH_FACTORS)

    if request.method == 'POST':
        form = QuoteForm(request.POST)
        if form.is_valid():
            # Get or create contact
            contact_name = form.cleaned_data['client_name']
            contact, _ = Contact.objects.get_or_create(
                name=contact_name,
                defaults={
                    'email':   form.cleaned_data.get('client_email', ''),
                    'phone':   form.cleaned_data.get('client_phone', ''),
                    'company': form.cleaned_data.get('client_company', ''),
                }
            )
            quote = form.save(commit=False)
            quote.contact = contact
            # Capture roof geometry for PDF plan diagram
            quote.roof_polygon_json  = request.POST.get('roof_polygon_json',  '')
            quote.roof_sections_json = request.POST.get('roof_sections_json', '')
            quote.save()

            # Build line items from rate card + submitted factors
            from decimal import Decimal as D
            material   = quote.material
            pitch_type = quote.pitch_type
            base_area  = float(quote.adjusted_area_sqm)  # flat × pitch × waste

            # Complexity & storey factors submitted by the form JS
            cx = float(request.POST.get('complexity_factor', '1.10'))
            st = float(request.POST.get('storey_factor',     '1.10'))
            eff_area = round(base_area * cx * st, 2)  # effective area for labour pricing

            # Toggle options
            def on(key): return request.POST.get(key, '0') == '1'

            sort = 1
            try:
                rc = RateCard.objects.get(material=material, pitch_type=pitch_type, is_active=True)
                cx_label = {1.00:'Simple',1.10:'Moderate',1.20:'Complex',1.35:'Very Complex'}.get(cx,'')
                st_label = {1.00:'Single storey',1.10:'Double storey',1.20:'3+ storeys'}.get(st,'')
                desc = f"{rc.description}"
                if cx_label or st_label:
                    desc += f" — {cx_label}, {st_label}" if cx_label and st_label else f" — {cx_label or st_label}"
                QuoteItem.objects.create(
                    quote=quote, description=desc,
                    quantity=eff_area, unit='m²',
                    unit_price_ex_gst=rc.rate_ex_gst, sort_order=sort,
                )
                sort += 1
            except RateCard.DoesNotExist:
                pass

            if eff_area > 0:
                # Removal & disposal
                if on('opt_remove'):
                    removal_rate = round(8.50 * st, 2)  # storey surcharge on labour
                    QuoteItem.objects.create(
                        quote=quote,
                        description='Removal & disposal of existing roofing material',
                        quantity=eff_area, unit='m²',
                        unit_price_ex_gst=removal_rate, sort_order=sort,
                    )
                    sort += 1

                # Sarking / underlay
                SARKING_RATE = {'colorbond': 8.00, 'zincalume': 7.00, 'terracotta': 9.00,
                                'concrete': 9.00, 'slate': 10.00, 'asphalt': 7.00, 'membrane': 0}
                if on('opt_sarking') and SARKING_RATE.get(material, 0) > 0:
                    QuoteItem.objects.create(
                        quote=quote,
                        description='Sarking / reflective foil underlay',
                        quantity=eff_area, unit='m²',
                        unit_price_ex_gst=SARKING_RATE.get(material, 8.00), sort_order=sort,
                    )
                    sort += 1

                # Wall & valley flashings (allow %)
                FLASHING_PCT = {'colorbond': 0.04, 'zincalume': 0.035, 'terracotta': 0.05,
                                'concrete': 0.05, 'slate': 0.055, 'asphalt': 0.04, 'membrane': 0.06}
                if on('opt_flashings'):
                    flash_allow = round(eff_area * 50 * FLASHING_PCT.get(material, 0.04), 2)
                    QuoteItem.objects.create(
                        quote=quote,
                        description='Wall & valley flashings — allowance',
                        quantity=1, unit='lot',
                        unit_price_ex_gst=flash_allow, sort_order=sort,
                    )
                    sort += 1

                # Scaffolding — always included
                QuoteItem.objects.create(
                    quote=quote,
                    description='Scaffolding & safety — site access package',
                    quantity=1, unit='lot',
                    unit_price_ex_gst=1200.00, sort_order=sort,
                )
                sort += 1

            # Log execution
            ExecutionLog.objects.create(
                tool_name='generate_quote',
                payload=json.dumps({'address': quote.property_address, 'material': material}),
                result=json.dumps({'quote_id': quote.id, 'ref': quote.ref_number}),
                quote=quote,
            )

            messages.success(request, f'Quote {quote.ref_number} created successfully.')
            return redirect('uc1:quote_detail', pk=quote.pk)
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = QuoteForm()

    return render(request, 'uc1_roofing/quote_create.html', {
        'form': form,
        'rate_cards': rate_cards,
        'pitch_factors_json': pitch_factors_json,
        'pitch_choices': PITCH_CHOICES,
        'material_choices': MATERIAL_CHOICES,
        'google_maps_api_key': settings.GOOGLE_MAPS_API_KEY,
    })


# ─── Quote Detail ─────────────────────────────────────────────────────────────

def quote_detail(request, pk):
    quote = get_object_or_404(Quote.objects.prefetch_related('items'), pk=pk)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_status':
            new_status = request.POST.get('status')
            if new_status in dict(Quote._meta.get_field('status').choices):
                quote.status = new_status
                quote.save()
                messages.success(request, f'Status updated to {quote.get_status_display()}.')
        elif action == 'add_item':
            desc  = request.POST.get('description', '').strip()
            qty   = request.POST.get('quantity', '0')
            unit  = request.POST.get('unit', 'm²')
            price = request.POST.get('unit_price_ex_gst', '0')
            if desc and qty and price:
                QuoteItem.objects.create(
                    quote=quote, description=desc,
                    quantity=float(qty), unit=unit,
                    unit_price_ex_gst=float(price),
                    sort_order=quote.items.count() + 1,
                )
                messages.success(request, 'Line item added.')
        elif action == 'delete_item':
            item_id = request.POST.get('item_id')
            QuoteItem.objects.filter(id=item_id, quote=quote).delete()
            messages.success(request, 'Line item removed.')
        return redirect('uc1:quote_detail', pk=quote.pk)

    return render(request, 'uc1_roofing/quote_detail.html', {'quote': quote})


# ─── Quote Print / PDF ────────────────────────────────────────────────────────

def quote_print(request, pk):
    quote = get_object_or_404(Quote.objects.prefetch_related('items'), pk=pk)
    return render(request, 'uc1_roofing/quote_print.html', {'quote': quote})


# ─── Rate Cards ───────────────────────────────────────────────────────────────

def rate_card_list(request):
    cards = RateCard.objects.all()
    return render(request, 'uc1_roofing/rate_cards.html', {
        'cards': cards,
        'form': RateCardForm(),
    })


def rate_card_create(request):
    if request.method == 'POST':
        form = RateCardForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Rate card created.')
        else:
            messages.error(request, 'Error creating rate card.')
    return redirect('uc1:rate_cards')


def rate_card_delete(request, pk):
    rc = get_object_or_404(RateCard, pk=pk)
    rc.delete()
    messages.success(request, 'Rate card deleted.')
    return redirect('uc1:rate_cards')


def rate_card_toggle(request, pk):
    rc = get_object_or_404(RateCard, pk=pk)
    rc.is_active = not rc.is_active
    rc.save()
    return redirect('uc1:rate_cards')


# ─── Contacts ─────────────────────────────────────────────────────────────────

def contact_list(request):
    contacts = Contact.objects.prefetch_related('quotes').all()
    return render(request, 'uc1_roofing/contacts.html', {'contacts': contacts})


# ─── Execution Log ────────────────────────────────────────────────────────────

def exec_log(request):
    logs = ExecutionLog.objects.select_related('quote').all()[:100]
    return render(request, 'uc1_roofing/exec_log.html', {'logs': logs})


# ─── AJAX: ML Building Footprint Lookup ──────────────────────────────────────

def _quadkey_for_point(lat: float, lon: float, zoom: int = MS_BUILDING_DATASET_ZOOM) -> str:
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    tile_x = int((lon + 180.0) / 360.0 * n)
    tile_y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)

    digits = []
    for i in range(zoom, 0, -1):
        digit = 0
        mask = 1 << (i - 1)
        if tile_x & mask:
            digit += 1
        if tile_y & mask:
            digit += 2
        digits.append(str(digit))
    return ''.join(digits)


def _polygon_area_sqm_lonlat(coords):
    if len(coords) < 3:
        return 0.0
    avg_lat = sum(c[1] for c in coords) / len(coords)
    radius = 6_371_000
    lat_m = radius * math.pi / 180
    lon_m = lat_m * math.cos(math.radians(avg_lat))
    area = 0.0
    for i in range(len(coords)):
        j = (i + 1) % len(coords)
        x1, y1 = coords[i][0] * lon_m, coords[i][1] * lat_m
        x2, y2 = coords[j][0] * lon_m, coords[j][1] * lat_m
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _get_ms_dataset_index():
    global _MS_DATASET_INDEX
    if _MS_DATASET_INDEX is not None:
        return _MS_DATASET_INDEX

    index = {}
    with urllib.request.urlopen(MS_BUILDING_DATASET_LINKS_URL, timeout=30) as resp:
        text = io.TextIOWrapper(resp, encoding='utf-8')
        for row in csv.DictReader(text):
            row = {str(k).strip(): str(v).strip() for k, v in row.items()}
            if row.get('Location', '').lower() != 'australia':
                continue
            quadkey = row.get('QuadKey', '')
            url = row.get('Url', '')
            if quadkey and url:
                index[quadkey] = url
    _MS_DATASET_INDEX = index
    return index


def _ensure_ms_tile_cache_table():
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS uc1_roofing_footprinttilecache (
                quadkey varchar(32) NOT NULL PRIMARY KEY,
                imported_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def _is_ms_tile_cached(quadkey: str) -> bool:
    from django.db import connection
    _ensure_ms_tile_cache_table()
    with connection.cursor() as cur:
        cur.execute("SELECT 1 FROM uc1_roofing_footprinttilecache WHERE quadkey = %s LIMIT 1", [quadkey])
        return cur.fetchone() is not None


def _mark_ms_tile_cached(quadkey: str):
    from django.db import connection
    _ensure_ms_tile_cache_table()
    with connection.cursor() as cur:
        cur.execute("INSERT OR IGNORE INTO uc1_roofing_footprinttilecache (quadkey) VALUES (%s)", [quadkey])


def _import_ms_tile_for_point(lat: float, lon: float) -> int:
    """
    Lazily cache the Microsoft ML building-footprint tile for a clicked QLD point.
    This gives statewide coverage without requiring a multi-hour full import up front.
    """
    quadkey = _quadkey_for_point(lat, lon)
    cache_key = f"{quadkey}:{lat:.3f}:{lon:.3f}"
    if _is_ms_tile_cached(cache_key):
        return 0

    tile_url = _get_ms_dataset_index().get(quadkey)
    if not tile_url:
        _mark_ms_tile_cached(cache_key)
        return 0

    rows = []
    nearby_lat_delta = 300 / 111_000
    nearby_lon_delta = 300 / (111_000 * max(abs(math.cos(math.radians(lat))), 0.001))
    with urllib.request.urlopen(tile_url, timeout=45) as resp:
        with gzip.GzipFile(fileobj=resp) as gz:
            for raw_line in gz:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                geom = obj.get('geometry') if isinstance(obj, dict) else None
                if not geom and obj.get('type') == 'Polygon':
                    geom = obj
                if not geom or geom.get('type') != 'Polygon':
                    continue

                outer = geom.get('coordinates', [[]])[0]
                if len(outer) < 4:
                    continue

                lons = [c[0] for c in outer]
                lats = [c[1] for c in outer]
                min_lat, max_lat = min(lats), max(lats)
                min_lon, max_lon = min(lons), max(lons)
                if max_lat < lat - nearby_lat_delta or min_lat > lat + nearby_lat_delta:
                    continue
                if max_lon < lon - nearby_lon_delta or min_lon > lon + nearby_lon_delta:
                    continue

                area = _polygon_area_sqm_lonlat(outer)
                if area < 10 or area > 10_000:
                    continue

                rows.append(BuildingFootprint(
                    min_lat=min_lat,
                    max_lat=max_lat,
                    min_lon=min_lon,
                    max_lon=max_lon,
                    centroid_lat=(min_lat + max_lat) / 2,
                    centroid_lon=(min_lon + max_lon) / 2,
                    area_sqm=round(area, 2),
                    geometry=json.dumps(outer),
                ))

    if rows:
        BuildingFootprint.objects.bulk_create(rows, batch_size=2000)
    _mark_ms_tile_cached(cache_key)
    return len(rows)


def _find_nearest_building_footprint(lat: float, lon: float):
    lat_delta = 50 / 111_000
    lon_delta = 50 / (111_000 * max(abs(math.cos(math.radians(lat))), 0.001))

    candidates = BuildingFootprint.objects.filter(
        min_lat__lte=lat + lat_delta,
        max_lat__gte=lat - lat_delta,
        min_lon__lte=lon + lon_delta,
        max_lon__gte=lon - lon_delta,
    )

    best = None
    best_dist_m = float('inf')
    for b in candidates:
        dlat = (b.centroid_lat - lat) * 111_000
        dlon = (b.centroid_lon - lon) * 111_000 * abs(math.cos(math.radians(lat)))
        dist = math.hypot(dlat, dlon)
        if dist < best_dist_m:
            best_dist_m = dist
            best = b

    if best is None or best_dist_m > 60:
        return None, best_dist_m
    return best, best_dist_m


def building_lookup(request):
    """
    Return the best building footprint nearest to a clicked lat/lon.

    Geoscape is tried first when configured. Microsoft ML footprints remain the
    local fallback so the app keeps working without paid Geoscape coverage.

    Query params: lat, lon
    Response (JSON):
        found       bool
        area_sqm    float   (only when found)
        geometry    [[lat,lon], ...]  Leaflet-ready (only when found)
        centroid    [lat, lon]        (only when found)
        count       int   — number of footprints in DB (for status display)
    """
    try:
        lat = float(request.GET.get('lat', 0))
        lon = float(request.GET.get('lon', 0))
    except (ValueError, TypeError):
        return JsonResponse({'found': False, 'error': 'Invalid coordinates'}, status=400)

    address = (request.GET.get('address') or '').strip()
    geoscape_message = ''
    try:
        geoscape = lookup_geoscape_building(lat, lon, address=address)
    except GeoscapeError as exc:
        geoscape = None
        geoscape_message = str(exc)
    except Exception as exc:
        geoscape = None
        geoscape_message = f'Geoscape lookup failed: {exc}'

    if geoscape:
        try:
            geoscape['count'] = BuildingFootprint.objects.count()
        except Exception:
            geoscape['count'] = 0
        geoscape['imported'] = 0
        return JsonResponse(geoscape)

    imported = 0
    try:
        total_before = BuildingFootprint.objects.count()
    except Exception:
        message = geoscape_message or 'Building footprint table not ready yet.'
        return JsonResponse({'found': False, 'count': 0, 'message': message})

    best, best_dist_m = _find_nearest_building_footprint(lat, lon)
    if best is None:
        try:
            imported = _import_ms_tile_for_point(lat, lon)
        except Exception as exc:
            return JsonResponse({
                'found': False,
                'count': total_before,
                'error': f'Footprint tile lookup failed: {exc}',
            }, status=502)
        best, best_dist_m = _find_nearest_building_footprint(lat, lon)

    total_after = BuildingFootprint.objects.count()
    if best is None:
        return JsonResponse({
            'found': False,
            'count': total_after,
            'imported': imported,
            'message': geoscape_message or 'No building footprint found within 60 m of the selected point.',
        })

    raw_coords = json.loads(best.geometry)
    leaflet_coords = [[c[1], c[0]] for c in raw_coords]
    return JsonResponse({
        'found': True,
        'area_sqm': round(best.area_sqm),
        'geometry': leaflet_coords,
        'centroid': [best.centroid_lat, best.centroid_lon],
        'distance_m': round(best_dist_m, 1),
        'count': total_after,
        'imported': imported,
        'source': 'microsoft',
        'source_label': 'Microsoft',
        'source_detail': 'microsoft_ml',
        'geoscape_message': geoscape_message,
    })

    # Total footprints available (so frontend can show helpful message when 0)
    try:
        total = BuildingFootprint.objects.count()
    except Exception:
        # Table doesn't exist yet (migration not run) — graceful fallback
        return JsonResponse({'found': False, 'count': 0,
                             'message': 'Building footprint table not ready yet.'})
    if total == 0:
        return JsonResponse({'found': False, 'count': 0,
                             'message': 'Building footprint DB is empty — run import_buildings.'})

    # Search within ~50 m of the click
    lat_delta = 50 / 111_000
    lon_delta = 50 / (111_000 * max(abs(math.cos(math.radians(lat))), 0.001))

    candidates = BuildingFootprint.objects.filter(
        min_lat__lte=lat + lat_delta,
        max_lat__gte=lat - lat_delta,
        min_lon__lte=lon + lon_delta,
        max_lon__gte=lon - lon_delta,
    )

    if not candidates.exists():
        return JsonResponse({'found': False, 'count': total})

    # Pick building whose bounding-box centre is closest to the click
    best = None
    best_dist_m = float('inf')
    for b in candidates:
        dlat = (b.centroid_lat - lat) * 111_000
        dlon = (b.centroid_lon - lon) * 111_000 * abs(math.cos(math.radians(lat)))
        dist = math.hypot(dlat, dlon)
        if dist < best_dist_m:
            best_dist_m = dist
            best = b

    # Reject if centroid is more than 60 m away (likely a different building)
    if best is None or best_dist_m > 60:
        return JsonResponse({'found': False, 'count': total})

    # Coordinates stored as [[lon, lat], ...] — convert to Leaflet [[lat, lon], ...]
    raw_coords = json.loads(best.geometry)
    leaflet_coords = [[c[1], c[0]] for c in raw_coords]

    return JsonResponse({
        'found':    True,
        'area_sqm': round(best.area_sqm),
        'geometry': leaflet_coords,
        'centroid': [best.centroid_lat, best.centroid_lon],
        'count':    total,
    })



# ─── Purchase: Vendor Comparison ─────────────────────────────────────────────

def purchase_compare(request, pk):
    """
    Show all active vendors side-by-side for the material on this quote.
    The user picks a vendor (or per-material vendor) then submits to create a PO.
    """
    quote = get_object_or_404(Quote.objects.prefetch_related('items'), pk=pk)
    material = quote.material

    # Gather all vendors that have a price for this material
    vendor_prices = (
        VendorMaterialPrice.objects
        .filter(material=material, is_available=True, vendor__is_active=True)
        .select_related('vendor')
        .order_by('unit_price_ex_gst')
    )

    # Build per-vendor context rows (price × quote area = estimated cost)
    from decimal import Decimal
    area = Decimal(str(quote.adjusted_area_sqm)) if quote.adjusted_area_sqm else Decimal('0')
    rows = []
    for vp in vendor_prices:
        est_ex  = round(vp.unit_price_ex_gst * area, 2) if area else Decimal('0')
        est_inc = round(est_ex * Decimal('1.10'), 2)
        rows.append({
            'vendor':   vp.vendor,
            'price':    vp,
            'est_ex':   est_ex,
            'est_inc':  est_inc,
        })

    # Also fetch existing POs for this quote so the user can see history
    existing_pos = quote.purchase_orders.select_related('vendor').all()

    price_min = rows[0]['est_inc']  if rows else 0
    price_max = rows[-1]['est_inc'] if rows else 0

    return render(request, 'uc1_roofing/purchase_compare.html', {
        'quote':          quote,
        'rows':           rows,
        'existing_pos':   existing_pos,
        'material_label': dict(MATERIAL_CHOICES).get(material, material),
        'price_min':      price_min,
        'price_max':      price_max,
    })


def purchase_order_create(request, pk):
    """POST: create a PO for the selected vendor."""
    if request.method != 'POST':
        return redirect('uc1:purchase_compare', pk=pk)

    quote     = get_object_or_404(Quote, pk=pk)
    vendor_id = request.POST.get('vendor_id')
    vendor    = get_object_or_404(Vendor, pk=vendor_id, is_active=True)

    delivery_date = request.POST.get('delivery_date') or None
    notes         = request.POST.get('notes', '').strip()

    # Create PO header
    po = PurchaseOrder.objects.create(
        quote=quote,
        vendor=vendor,
        status='draft',
        delivery_address=quote.property_address,
        requested_delivery_date=delivery_date,
        notes=notes,
    )

    # Pull vendor price for the quote's material and build line items
    area = float(quote.adjusted_area_sqm)
    material = quote.material
    sort = 1

    try:
        vp = VendorMaterialPrice.objects.get(vendor=vendor, material=material)
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            description=vp.description,
            item_code=vp.item_code,
            quantity=area,
            unit=vp.unit,
            unit_price_ex_gst=vp.unit_price_ex_gst,
            sort_order=sort,
        )
        sort += 1
    except VendorMaterialPrice.DoesNotExist:
        pass

    # Add accessory line items derived from quote items (screws, ridge, guttering)
    ACCESSORY_RATES = [
        ('Ridge capping',          1,      'lot',  320.00),
        ('Fasteners & screws',     area,   'm²',     1.80),
        ('Flashing & sealant kit', 1,      'lot',  210.00),
    ]
    for desc, qty, unit, price in ACCESSORY_RATES:
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            description=desc,
            quantity=qty,
            unit=unit,
            unit_price_ex_gst=price,
            sort_order=sort,
        )
        sort += 1

    # Audit log
    ExecutionLog.objects.create(
        tool_name='create_purchase_order',
        payload=json.dumps({
            'quote_ref':  quote.ref_number,
            'vendor':     vendor.name,
            'material':   material,
            'area_sqm':   area,
        }),
        result=json.dumps({
            'po_number': po.po_number,
            'total_inc_gst': po.total_inc_gst,
        }),
        status='success',
        quote=quote,
    )

    messages.success(request, f'Purchase Order {po.po_number} created for {vendor.name}.')
    return redirect('uc1:purchase_order_detail', po_pk=po.pk)


def purchase_order_detail(request, po_pk):
    po = get_object_or_404(
        PurchaseOrder.objects.select_related('vendor', 'quote').prefetch_related('po_items'),
        pk=po_pk,
    )

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_status':
            new_status = request.POST.get('status')
            valid = [s[0] for s in PurchaseOrder._meta.get_field('status').choices]
            if new_status in valid:
                po.status = new_status
                po.save()
                # Log status change
                ExecutionLog.objects.create(
                    tool_name='update_po_status',
                    payload=json.dumps({'po_number': po.po_number, 'new_status': new_status}),
                    result=json.dumps({'ok': True}),
                    status='success',
                    quote=po.quote,
                )
                messages.success(request, f'Status updated to {po.get_status_display()}.')
        return redirect('uc1:purchase_order_detail', po_pk=po.pk)

    return render(request, 'uc1_roofing/purchase_order_detail.html', {'po': po})


def purchase_order_print(request, po_pk):
    po = get_object_or_404(
        PurchaseOrder.objects.select_related('vendor', 'quote').prefetch_related('po_items'),
        pk=po_pk,
    )
    return render(request, 'uc1_roofing/purchase_order_print.html', {'po': po})


def purchase_order_list(request):
    pos = PurchaseOrder.objects.select_related('vendor', 'quote').all()
    return render(request, 'uc1_roofing/purchase_order_list.html', {'pos': pos})



# ─── Price Check Log ─────────────────────────────────────────────────────────

def price_check_log(request):
    logs   = PriceCheckLog.objects.all()[:50]
    latest = logs.first() if logs else None
    # Price movements: all items where previous_price is set
    recent_changes = (
        VendorMaterialPrice.objects
        .filter(previous_price__isnull=False)
        .select_related('vendor')
        .order_by('-updated_at')[:20]
    )
    return render(request, 'uc1_roofing/price_check_log.html', {
        'logs':           logs,
        'latest':         latest,
        'recent_changes': recent_changes,
    })



# ─── AJAX: Vendor Prices (for price monitor table) ───────────────────────────

def api_vendor_prices(request):
    items = (
        VendorMaterialPrice.objects
        .filter(is_available=True, vendor__is_active=True)
        .select_related('vendor')
        .order_by('vendor__name', 'material')
    )
    return JsonResponse({'prices': [
        {
            'vendor':        vmp.vendor.name,
            'material':      vmp.get_material_display(),
            'item_code':     vmp.item_code,
            'price':         str(vmp.unit_price_ex_gst),
            'unit':          vmp.unit,
            'lead_days':     vmp.lead_days,
            'last_verified': vmp.last_verified.strftime('%d %b %Y') if vmp.last_verified else None,
            'source_url':    vmp.price_source_url or None,
        }
        for vmp in items
    ]})


# ─── AJAX: LiDAR / Roof Analysis ─────────────────────────────────────────────

@csrf_exempt
def lidar_analyze(request):
    """
    POST /uc1/api/lidar-analyze/
    Body (JSON):
      { lat, lng, polygon: [[lat,lng],...], storeys, solar_panels, solar_hw }

    Runs the full roof analysis (perimeter, heights, scaffolding, structures)
    and saves the result as a RoofLidarAnalysis linked to the quote (optional).
    Returns JSON summary immediately.
    """
    from .lidar_service import full_roof_analysis

    try:
        body = json.loads(request.body)
        lat          = float(body['lat'])
        lng          = float(body['lng'])
        polygon      = body.get('polygon', [])
        storeys      = int(body.get('storeys', 1))
        solar_panels = bool(body.get('solar_panels', False))
        solar_hw     = bool(body.get('solar_hw', False))
        quote_id     = body.get('quote_id')

        if len(polygon) < 3:
            return JsonResponse({'error': 'polygon must have at least 3 points'}, status=400)

        result = full_roof_analysis(
            lat=lat, lng=lng,
            polygon_coords=polygon,
            storeys=storeys,
            solar_panels=solar_panels,
            solar_hw=solar_hw,
        )

        # Persist if quote_id provided
        if quote_id:
            try:
                quote = Quote.objects.get(pk=quote_id)
                scaff = result['scaffolding']
                RoofLidarAnalysis.objects.update_or_create(
                    quote=quote,
                    defaults={
                        'perimeter_m':        result['perimeter_m'],
                        'guttering_linear_m': result['guttering_linear_m'],
                        'ridge_height_m':     result.get('ridge_height_m'),
                        'eave_height_m':      result.get('eave_height_m'),
                        'height_range_m':     result.get('height_range_m'),
                        'scaffolding_required':   scaff['required'],
                        'scaffolding_linear_m':   scaff.get('estimated_linear_m', 0),
                        'scaffolding_risk_level': scaff.get('risk_level', 'low'),
                        'scaffolding_reason':     scaff.get('reason', ''),
                        'structure_count':    result.get('structure_count', 1),
                        'structures_json':    json.dumps(result.get('structures', [])),
                        'solar_panels':       result['solar_panels'],
                        'solar_hw':           result['solar_hw'],
                        'lidar_coverage':     result['lidar_coverage'],
                        'data_source':        result['data_source'],
                        'analysis_notes':     json.dumps(result['analysis_notes']),
                        'elapsed_ms':         result['elapsed_ms'],
                    }
                )
            except Quote.DoesNotExist:
                pass

        return JsonResponse(result)

    except (KeyError, ValueError, json.JSONDecodeError) as e:
        return JsonResponse({'error': str(e)}, status=400)


# ─── AJAX: Solar API Analysis ────────────────────────────────────────────────

def _roof_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _truthy_geo_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in {'yes', 'true', '1', 'y'}


def _roof_slope_category(pitch_deg: float) -> str:
    if pitch_deg < 5:
        return 'flat'
    if pitch_deg < 15:
        return 'low'
    if pitch_deg < 25:
        return 'medium'
    if pitch_deg < 35:
        return 'steep'
    return 'very steep'


def _default_pitch_for_shape(shape: str) -> float:
    shape = str(shape or '').strip().lower()
    if 'flat' in shape:
        return 3.0
    if 'skillion' in shape or 'shed' in shape:
        return 10.0
    if 'gable' in shape or 'hip' in shape:
        return 22.5
    return 20.0


def _latlng_perimeter_m(poly: list) -> float:
    if not isinstance(poly, list) or len(poly) < 3:
        return 0.0
    total = 0.0
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        if not isinstance(p, (list, tuple)) or not isinstance(q, (list, tuple)):
            continue
        if len(p) < 2 or len(q) < 2:
            continue
        lat1, lon1 = _roof_float(p[0]), _roof_float(p[1])
        lat2, lon2 = _roof_float(q[0]), _roof_float(q[1])
        if None in (lat1, lon1, lat2, lon2):
            continue
        radius = 6_371_000
        p1 = math.radians(lat1)
        p2 = math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        total += radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return total


def _latlng_bbox(poly: list) -> dict:
    clean = [
        (_roof_float(p[0]), _roof_float(p[1]))
        for p in (poly or [])
        if isinstance(p, (list, tuple)) and len(p) >= 2
    ]
    clean = [(lat, lng) for lat, lng in clean if lat is not None and lng is not None]
    if not clean:
        return {'sw': {'lat': 0, 'lng': 0}, 'ne': {'lat': 0, 'lng': 0}}
    lats = [p[0] for p in clean]
    lngs = [p[1] for p in clean]
    return {
        'sw': {'lat': min(lats), 'lng': min(lngs)},
        'ne': {'lat': max(lats), 'lng': max(lngs)},
    }


def _estimate_solar_capacity_from_roof(roof_area_m2: float, existing_daily_kwh: float | None = None) -> dict:
    panel_unit_m2 = 1.96
    panel_cap_w = 400.0
    usable_roof_area = max(0.0, roof_area_m2 * 0.55)
    max_panels = int(usable_roof_area // panel_unit_m2)
    max_cap_kw = round(max_panels * panel_cap_w / 1000, 2)
    max_area_m2 = round(max_panels * panel_unit_m2, 1)

    typ_panels = int(round(max_panels * 0.66)) if max_panels else 0
    typ_cap_kw = round(typ_panels * panel_cap_w / 1000, 2)
    typ_area_m2 = round(typ_panels * panel_unit_m2, 1)

    # Conservative QLD planning estimate only. Google Solar kWh remains preferred
    # where available because it uses roof-specific sunlight modelling.
    qld_yield_kwh_per_kw_year = 1450
    max_kwh_yr = round(max_cap_kw * qld_yield_kwh_per_kw_year, 0)
    typ_kwh_yr = round(typ_cap_kw * qld_yield_kwh_per_kw_year, 0)
    existing_kwh_yr = round(existing_daily_kwh * 365, 0) if existing_daily_kwh else 0

    return {
        'solar_panel_unit_m2': panel_unit_m2,
        'solar_panel_cap_w': panel_cap_w,
        'solar_panel_life_yr': 20,
        'solar_max_shine_hr': 0.0,
        'solar_co2_kg_mwh': 0.0,
        'solar_max_panels': max_panels,
        'solar_max_area_m2': max_area_m2,
        'solar_max_cap_kw': max_cap_kw,
        'solar_max_kwh_yr': max_kwh_yr,
        'solar_typ_panels': typ_panels,
        'solar_typ_area_m2': typ_area_m2,
        'solar_typ_cap_kw': typ_cap_kw,
        'solar_typ_kwh_yr': typ_kwh_yr,
        'solar_existing_kwh_yr': existing_kwh_yr,
        'solar_capacity_estimated': True,
        'solar_yield_basis': 'Estimated at 1450 kWh/kW/year; Google Solar remains preferred for production modelling',
    }


def _geoscape_roof_analysis(
    lat: float,
    lng: float,
    storeys: int = 1,
    solar_panels: bool = False,
    solar_hw: bool = False,
    address: str = '',
    google_solar_error: str = '',
) -> dict | None:
    t0 = time.time()
    try:
        building = lookup_geoscape_building(lat, lng, address=address)
    except Exception:
        return None
    if not building:
        return None

    footprint = building.get('geometry') or []
    ground_area = _roof_float(building.get('area_sqm'), 0.0) or 0.0
    if ground_area <= 0 and footprint:
        ground_area = _polygon_area_sqm_lonlat([[p[1], p[0]] for p in footprint])

    pitch = _roof_float(building.get('roof_slope'))
    if pitch is None or pitch < 0:
        pitch = _default_pitch_for_shape(building.get('roof_shape'))
    pitch = max(0.0, min(float(pitch), 60.0))
    pitch_rad = math.radians(pitch)
    slope_factor = 1 / max(math.cos(pitch_rad), 0.2)
    total_area = ground_area * slope_factor if ground_area > 0 else 0.0

    perimeter = _latlng_perimeter_m(footprint)
    eave_height = _roof_float(building.get('eave_height_m'))
    if eave_height is None:
        eave_height = {1: 2.7, 2: 5.5, 3: 8.5}.get(storeys, 2.7)

    ridge_height = _roof_float(building.get('roof_height_m'))
    if ridge_height is None or ridge_height <= eave_height:
        span_m = math.sqrt(ground_area / 1.5) if ground_area > 0 else 6.0
        ridge_height = eave_height + (span_m / 2) * math.tan(pitch_rad)

    solar_flag = _truthy_geo_flag(building.get('solar_panel')) or _truthy_geo_flag(building.get('solar_flag'))
    existing_solar_area = _roof_float(building.get('solar_area_m2'), 0.0) or 0.0
    daily_power = _roof_float(building.get('solar_daily_estimated_power_kwh'), 0.0) or 0.0
    capacity = _estimate_solar_capacity_from_roof(total_area, daily_power)
    centroid = building.get('centroid') or [lat, lng]
    bbox = _latlng_bbox(footprint)
    section = {
        'pitch_deg': round(pitch, 1),
        'azimuth_deg': 0.0,
        'area_m2': round(total_area, 2),
        'ground_area_m2': round(ground_area, 2),
        'center': {'lat': _roof_float(centroid[0], lat), 'lng': _roof_float(centroid[1], lng)},
        'bbox': bbox,
        'facing': 'mixed',
        'slope_category': _roof_slope_category(pitch),
        'geo_polygon': footprint,
        'source': 'geoscape',
        'source_note': 'Geoscape footprint with roof attributes; facet split is estimated',
    }

    roof_material = str(building.get('roof_material') or '').strip()
    roof_material_display, roof_material_note, roof_material_confidence = _roof_material_display(roof_material)

    notes = [
        'Google Solar did not return usable roof sections; using Geoscape Buildings fallback',
        'Geoscape provides roof outline and attributes, but not Google-style roofSegmentStats or solarPanelConfigs',
    ]
    if roof_material:
        notes.append(f'Roof material from Geoscape: {roof_material_display}')
    if building.get('capture_resolution') or building.get('capture_method'):
        bits = [str(v) for v in (building.get('capture_resolution'), building.get('capture_method')) if v]
        notes.append('Geoscape capture source: ' + ' '.join(bits))
    if google_solar_error:
        notes.append(f'Google Solar response: {google_solar_error}')

    scaffolding_required = (eave_height or 0) > 3.0
    result = {
        'ok': True,
        'error': None,
        'data_source': 'geoscape_buildings',
        'lidar_coverage': 'geoscape',
        'source_label': 'Geoscape',
        'source_detail': building.get('source_detail') or 'geoscape',
        'google_solar_error': google_solar_error,
        'imagery_date': building.get('solar_capture_date') or '',
        'imagery_quality': building.get('capture_resolution') or building.get('capture_method') or 'GEOSCAPE',
        'capture_resolution': building.get('capture_resolution') or building.get('solar_capture_resolution') or '',
        'capture_method': building.get('capture_method') or building.get('solar_capture_method') or '',
        'section_count': 1,
        'total_area_m2': round(total_area, 2),
        'ground_area_m2': round(ground_area, 2),
        'dominant_pitch_deg': round(pitch, 1),
        'perimeter_m': round(perimeter, 2) if perimeter else None,
        'guttering_linear_m': round(perimeter, 2) if perimeter else None,
        'eave_lm': round(perimeter, 2) if perimeter else None,
        'ridge_lm': 0.0,
        'valley_lm': 0.0,
        'hip_lm': 0.0,
        'rake_lm': 0.0,
        'boundary_confidence': 'MEDIUM',
        'eave_height_m': round(eave_height, 2) if eave_height is not None else None,
        'ridge_height_m': round(ridge_height, 2) if ridge_height is not None else None,
        'roof_shape': building.get('roof_shape') or '',
        'roof_material': roof_material,
        'roof_material_display': roof_material_display,
        'roof_material_source': 'Geoscape primary_roof_material' if roof_material else '',
        'roof_material_confidence': roof_material_confidence,
        'roof_material_note': roof_material_note,
        'roof_colour': building.get('roof_colour') or '',
        'building_use': building.get('building_use') or '',
        'building_pid': building.get('building_pid') or '',
        'footprint': footprint,
        'solar_panels': bool(solar_panels or solar_flag),
        'solar_hw': solar_hw,
        'solar_panels_detected': bool(solar_flag),
        'solar_existing_area_m2': round(existing_solar_area, 2),
        'solar_existing_daily_kwh': round(daily_power, 2),
        'solar_capture_date': building.get('solar_capture_date') or '',
        'solar_capture_resolution': building.get('solar_capture_resolution') or '',
        'solar_capture_method': building.get('solar_capture_method') or '',
        'scaffolding': {
            'required': scaffolding_required,
            'estimated_linear_m': round(perimeter, 2) if perimeter else 0,
            'risk_level': 'high' if (eave_height or 0) > 6.0 else 'medium' if scaffolding_required else 'low',
            'reason': f"Geoscape eave height ~{eave_height:.1f} m" if eave_height else 'Storey-based estimate',
        },
        'sections': [section],
        'analysis_notes': notes,
        'elapsed_ms': round((time.time() - t0) * 1000),
    }
    result.update(capacity)
    return result


def _roof_material_display(material: str) -> tuple[str, str, str]:
    raw = str(material or '').strip()
    if not raw:
        return 'Unknown', 'No roof material returned by the available data source.', 'unknown'

    lower = raw.lower()
    if 'metal' in lower:
        return (
            'Metal (Decramastic possible)',
            'Geoscape classifies this broadly as Metal; verify visually if it is Decramastic/pressed metal tile.',
            'medium',
        )
    if 'tile' in lower:
        return (
            'Tile',
            'Geoscape classifies this broadly as Tile; verify visually where metal tile/decramastic is suspected.',
            'medium',
        )
    if 'concrete' in lower:
        return ('Flat concrete', 'Geoscape classifies this as Flat Concrete.', 'medium')
    if 'fiberglass' in lower or 'plastic' in lower:
        return ('Fiberglass/Plastic', 'Geoscape classifies this as Fiberglass/Plastic.', 'medium')
    return (raw, 'Roof material from Geoscape primary_roof_material.', 'medium')


def _ensure_roof_material_fields(result: dict) -> dict:
    material = str(result.get('roof_material') or '').strip()
    display, note, confidence = _roof_material_display(material)
    result.setdefault('roof_material', material)
    result.setdefault('roof_material_display', display)
    result.setdefault('roof_material_source', '')
    result.setdefault('roof_material_confidence', confidence)
    result.setdefault('roof_material_note', note)
    return result


def _merge_geoscape_roof_material(result: dict, lat: float, lng: float, address: str = '') -> dict:
    try:
        building = lookup_geoscape_building(lat, lng, address=address)
    except Exception:
        return _ensure_roof_material_fields(result)
    if not building:
        return _ensure_roof_material_fields(result)

    material = str(building.get('roof_material') or '').strip()
    display, note, confidence = _roof_material_display(material)
    result['roof_material'] = material
    result['roof_material_display'] = display
    result['roof_material_source'] = 'Geoscape primary_roof_material' if material else ''
    result['roof_material_confidence'] = confidence
    result['roof_material_note'] = note
    if building.get('roof_colour'):
        result['roof_colour'] = building.get('roof_colour')
    if building.get('roof_shape') and not result.get('roof_shape'):
        result['roof_shape'] = building.get('roof_shape')

    notes = result.get('analysis_notes')
    if not isinstance(notes, list):
        notes = [str(notes)] if notes else []
    if material:
        notes.append(f'Roof material from Geoscape: {display}')
    result['analysis_notes'] = notes
    return result


@csrf_exempt
def solar_analyze(request):
    """
    POST /uc1/api/solar-analyze/
    Body (JSON):
      { lat, lng, storeys, solar_panels, solar_hw, address }

    Calls Google Solar API first. If Google has no usable roof sections, fall
    back to Geoscape Buildings roof outline/attributes where configured.
    """
    from .solar_api_service import full_solar_analysis

    try:
        body = json.loads(request.body)
        lat          = float(body['lat'])
        lng          = float(body['lng'])
        storeys      = int(body.get('storeys', 1))
        solar_panels = bool(body.get('solar_panels', False))
        solar_hw     = bool(body.get('solar_hw', False))
        address      = (body.get('address') or '').strip()

        result = full_solar_analysis(
            lat=lat, lng=lng,
            storeys=storeys,
            solar_panels=solar_panels,
            solar_hw=solar_hw,
        )
        has_solar_geometry = (
            result.get('ok') is not False and
            int(result.get('section_count') or 0) > 0 and
            float(result.get('ground_area_m2') or 0) > 0
        )
        if has_solar_geometry:
            return JsonResponse(_merge_geoscape_roof_material(result, lat, lng, address=address))

        geoscape = _geoscape_roof_analysis(
            lat=lat, lng=lng,
            storeys=storeys,
            solar_panels=solar_panels,
            solar_hw=solar_hw,
            address=address,
            google_solar_error=result.get('error') or '',
        )
        if geoscape:
            return JsonResponse(geoscape)

        return JsonResponse(_ensure_roof_material_fields(result))

    except (KeyError, ValueError, json.JSONDecodeError) as e:
        return JsonResponse({'error': str(e), 'ok': False}, status=400)


# ─── AJAX: AI Roof Drawing — satellite screenshot → Claude Vision ─────────────

ROOF_VISION_SYSTEM = """You are an expert roofing estimator analyzing a top-down satellite image of a property in Queensland, Australia.

Your task is to draw the complete roof outline and identify every distinct roof section
(slope/facet) visible on the SINGLE selected building only — the one containing the yellow dot.

═══ OUTLINE TIGHTNESS — CRITICAL ═══
Every vertex of roof_outline MUST lie on PHYSICAL ROOF MATERIAL (tile, metal sheet, membrane,
solar panels installed on the roof). If a vertex would land on grass, dirt, driveway, road,
trees, shadow, pool, neighbour's roof, or empty ground, MOVE IT INWARD until it touches
the actual roof edge.

Before returning JSON, test every vertex: "Is this pixel ROOF or NOT-ROOF?" If NOT-ROOF,
shrink the polygon. A roof outline that includes ANY non-roof area is WRONG.

Prefer too-tight over too-loose. A 1-metre underestimate on outline area is far better
than a 5-metre overestimate that drags grass into the polygon.

═══ SINGLE-ROOF RULE ═══
The image may show multiple buildings, sheds, or structures.
You MUST draw roof_outline and sections for ONLY the one building where the yellow dot sits.
Do NOT include any other structure, even if it is adjacent, larger, or brighter.

═══ HOW TO IDENTIFY SECTIONS — use the ridge-line method ═══
The correct way to find sections is to trace the visible ridge and valley lines first,
then define one section per enclosed surface between those lines.

Step 1 — Find the ridge lines. Look for:
  • MAIN RIDGE: the brightest/highest horizontal line running along the roof peak.
  • HIP RIDGES: diagonal lines radiating from the ends of the main ridge down to the
    roof corners.
  • VALLEY LINES: inward V-shaped lines where two roof wings meet going downward.

Step 2 — Count sections. Each enclosed surface bounded by ridge lines, hip ridges,
valley lines, and the eave (roof edge) is exactly ONE section.

═══ DO NOT DEFAULT TO A 4-WAY HIP PATTERN ═══
Many Queensland houses are NOT simple symmetric hips. Before drawing 4 sections meeting
at a central point, verify ALL FOUR hip ridges are individually visible in the image.
If you only see two hip ridges, draw fewer sections. If you see no ridge structure at all,
draw 1 section (flat or skillion) and set confidence to "low".

A WRONG 4-section symmetric hip pattern is a common failure mode — only draw it when
you can literally trace each hip ridge from peak to corner in the image pixels.

Common section counts:
  • Simple gable: 2 sections
  • Skillion / mono-pitch: 1 section
  • Hip roof: 4 sections — ONLY when 4 hip ridges are individually visible
  • L-shaped hip: 6–8 sections
  • Complex multi-wing: 6–12 sections

Do NOT create extra sections from:
  • Colour variation or shadow within one slope
  • Solar panels sitting flat on a slope
  • Fascia boards, gutters, or roof vents

Rules:
- North is UP in the image
- Return polygon vertices as PERCENTAGE coordinates: x% of width, y% of height (top-left origin)
- Do not include adjacent roofs, neighbouring dwellings, carports, sheds, trees, pools, or roads
- facing: the compass direction the slope DRAINS toward (N/NE/E/SE/S/SW/W/NW)
- pitch_est: estimated pitch in degrees (typical QLD: 15–30°; flat metal: <5°)
- If you cannot clearly see the roof sections, still return your best estimate

Respond with ONLY valid JSON, no explanation, no markdown fences. Format:
{
  "roof_outline": [[x1,y1],[x2,y2],[x3,y3],...],
  "sections": [
    {
      "id": 1,
      "label": "North slope",
      "facing": "N",
      "pitch_est": 22,
      "polygon": [[x1,y1],[x2,y2],[x3,y3],...],
      "notes": ""
    }
  ],
  "roof_type": "hip|gable|flat|complex",
  "confidence": "high|medium|low",
  "notes": "overall observation"
}"""

ROOF_OUTLINE_SYSTEM = """You are an expert roofing estimator. You receive TWO satellite images:
  Image 1 — CONTEXT view (wider zoom): shows the building in its street/neighbourhood context.
  Image 2 — DETAIL view (closer zoom): shows the target roof more closely.

Your ONLY task is to trace the roof outline of the SINGLE building marked by the yellow dot in Image 2.

Rules:
- The yellow dot identifies the exact building to outline.
- Trace tightly around ONLY that connected roof — within 1-2 metres of the visible roof edge.
- Do NOT include any neighbouring building, shed, carport, driveway, or tree.
- Use Image 1 (context) to see where this roof ends and the next building or gap begins.
- Return polygon vertices as PERCENTAGE coordinates of Image 2 (the detail image):
  x% of its width, y% of its height. Origin = top-left corner. North is UP.

Respond with ONLY valid JSON, no explanation, no markdown fences:
{
  "roof_outline": [[x1,y1],[x2,y2],[x3,y3],...],
  "confidence": "high|medium|low",
  "notes": "brief observation"
}"""


FACING_COLORS_PY = {
    'N': '#3B82F6', 'NE': '#06B6D4', 'E': '#F59E0B', 'SE': '#EF4444',
    'S': '#F97316', 'SW': '#EC4899', 'W': '#8B5CF6', 'NW': '#10B981',
}


def _clean_geo_polygon_latlng(poly: list) -> list:
    cleaned = []
    if not isinstance(poly, list):
        return cleaned
    for p in poly:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        try:
            lat = float(p[0])
            lng = float(p[1])
        except (TypeError, ValueError):
            continue
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            cleaned.append([lat, lng])
    return cleaned if len(cleaned) >= 3 else []


def _roof_static_map_view(lat: float, lng: float, width: int, height: int,
                          fallback_zoom: int = 20, max_zoom: int = 20,
                          known_roof_area_m2: float = 0,
                          known_ground_area_m2: float = 0,
                          use_ms_guide: bool = False,
                          focus_polygon: list | None = None) -> dict:
    """
    Choose a Google Static Maps view that tightly frames the selected roof.
    Uses the nearest Microsoft footprint when available, otherwise falls back
    to the clicked point and requested zoom.
    """
    footprint = []
    focus_polygon = _clean_geo_polygon_latlng(focus_polygon or [])
    center_lat, center_lng = lat, lng
    max_zoom = min(int(max_zoom or 20), 20)
    fallback_zoom = min(int(fallback_zoom or 20), max_zoom)
    zoom = fallback_zoom
    guide_area_sqm = 0
    footprint_source = ''

    best = None
    if focus_polygon:
        footprint = focus_polygon
        footprint_source = 'selected_outline'
        lats = [p[0] for p in footprint]
        lngs = [p[1] for p in footprint]
        center_lat = (min(lats) + max(lats)) / 2
        center_lng = (min(lngs) + max(lngs)) / 2
        guide_area_sqm = _polygon_area_sqm_lonlat([[p[1], p[0]] for p in footprint])
    elif use_ms_guide:
        best, _dist = _find_nearest_building_footprint(lat, lng)
        if best is None:
            try:
                _import_ms_tile_for_point(lat, lng)
                best, _dist = _find_nearest_building_footprint(lat, lng)
            except Exception:
                best = None

    if best is not None:
        raw_coords = json.loads(best.geometry)
        footprint = [[c[1], c[0]] for c in raw_coords]
        guide_area_sqm = float(best.area_sqm or 0)
        lats = [p[0] for p in footprint]
        lngs = [p[1] for p in footprint]
        center_lat = (min(lats) + max(lats)) / 2
        center_lng = (min(lngs) + max(lngs)) / 2
        footprint_source = 'microsoft'

    roof_hint = float(known_roof_area_m2 or 0)
    ground_hint = float(known_ground_area_m2 or 0)
    if footprint:
        area_hint = max(guide_area_sqm, 90.0)
    else:
        sane_ground = ground_hint if 20 <= ground_hint <= 1200 else 0
        sane_roof = roof_hint * 0.82 if 20 <= roof_hint <= 1500 else 0
        area_hint = max(sane_ground, sane_roof, 160.0)

    guide_span_m = 0
    if len(footprint) >= 3:
        lats = [p[0] for p in footprint]
        lngs = [p[1] for p in footprint]
        cos_lat = max(abs(math.cos(math.radians(center_lat))), 0.001)
        guide_span_m = max(
            (max(lats) - min(lats)) * 111_320,
            (max(lngs) - min(lngs)) * 111_320 * cos_lat,
        )

    # If known roof area is much bigger than the Microsoft guide, assume the
    # guide is incomplete and center on the clicked roof point with wider context.
    if footprint_source == 'microsoft' and guide_area_sqm and area_hint > guide_area_sqm * 1.55:
        center_lat, center_lng = lat, lng

    if footprint:
        target_span_m = max(24.0, math.sqrt(area_hint) * 1.9, guide_span_m * 1.45)
        target_span_m = min(target_span_m, 120.0)
    else:
        target_span_m = max(28.0, math.sqrt(area_hint) * 2.2)
        target_span_m = min(target_span_m, 80.0)

    chosen = max(17, min(max_zoom, fallback_zoom))
    for z in range(max_zoom, 16, -1):
        meters_per_px_z = 156543.03392 * math.cos(math.radians(center_lat)) / (2 ** z)
        visible_span_m = min(width, height) * meters_per_px_z
        if target_span_m <= visible_span_m * 0.90:
            chosen = z
            break
    zoom = chosen

    meters_per_px = 156543.03392 * math.cos(math.radians(center_lat)) / (2 ** zoom)
    return {
        'center_lat': center_lat,
        'center_lng': center_lng,
        'zoom': zoom,
        'meters_per_px': meters_per_px,
        'footprint': footprint,
        'footprint_source': footprint_source,
        'guide_area_sqm': guide_area_sqm,
        'target_span_m': target_span_m,
    }


def _static_map_pixel(lat: float, lng: float, center_lat: float, center_lng: float,
                      zoom: int, width: int, height: int) -> tuple[float, float]:
    def world_point(a_lat, a_lng):
        siny = max(-0.9999, min(0.9999, math.sin(math.radians(a_lat))))
        scale = 256 * (2 ** zoom)
        return (
            (a_lng + 180) / 360 * scale,
            (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)) * scale,
        )

    x, y = world_point(lat, lng)
    cx, cy = world_point(center_lat, center_lng)
    return x - cx + width / 2, y - cy + height / 2


def _static_map_latlng(x: float, y: float, center_lat: float, center_lng: float,
                       zoom: int, width: int, height: int) -> tuple[float, float]:
    siny = max(-0.9999, min(0.9999, math.sin(math.radians(center_lat))))
    scale = 256 * (2 ** zoom)
    center_x = (center_lng + 180) / 360 * scale
    center_y = (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)) * scale
    world_x = center_x + x - width / 2
    world_y = center_y + y - height / 2
    lng = world_x / scale * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * world_y / scale))))
    return lat, lng


def _expand_crop_box(left: float, top: float, right: float, bottom: float,
                     img_w: int, img_h: int, min_px: int = 300) -> tuple[int, int, int, int]:
    cx = (left + right) / 2
    cy = (top + bottom) / 2
    crop_w = max(right - left, min_px)
    crop_h = max(bottom - top, min_px)
    crop_w = min(crop_w, img_w)
    crop_h = min(crop_h, img_h)

    left = max(0, min(img_w - crop_w, cx - crop_w / 2))
    top = max(0, min(img_h - crop_h, cy - crop_h / 2))
    right = left + crop_w
    bottom = top + crop_h
    return int(math.floor(left)), int(math.floor(top)), int(math.ceil(right)), int(math.ceil(bottom))


def _crop_static_map_to_selected_roof(img_bytes: bytes, map_view: dict,
                                      click_lat: float, click_lng: float,
                                      width: int, height: int) -> tuple[bytes, int, int, dict]:
    """
    Static Maps must be requested as a square, but Claude should see only the
    selected roof. Crop the fetched image around the selected outline when one
    is available, otherwise around the clicked roof point.
    """
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(img_bytes)).convert('RGB')
        img_w, img_h = image.size
        meters_per_px = float(map_view.get('meters_per_px') or 0) or 0.1
        footprint = map_view.get('footprint') or []
        points = []

        if len(footprint) >= 3:
            points.extend(
                _static_map_pixel(
                    p[0], p[1],
                    map_view['center_lat'], map_view['center_lng'],
                    map_view['zoom'], width, height,
                )
                for p in footprint
            )

        click_px = _static_map_pixel(
            click_lat, click_lng,
            map_view['center_lat'], map_view['center_lng'],
            map_view['zoom'], width, height,
        )
        points.append(click_px)

        if len(points) >= 2:
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            roof_span_px = max(max(xs) - min(xs), max(ys) - min(ys))
            margin_px = max(44, roof_span_px * 0.32, 8 / meters_per_px)
            left = min(xs) - margin_px
            right = max(xs) + margin_px
            top = min(ys) - margin_px
            bottom = max(ys) + margin_px
        else:
            cx, cy = click_px
            crop_span_px = (float(map_view.get('target_span_m') or 42) / meters_per_px)
            crop_span_px = max(300, min(crop_span_px, min(img_w, img_h)))
            left = cx - crop_span_px / 2
            right = cx + crop_span_px / 2
            top = cy - crop_span_px / 2
            bottom = cy + crop_span_px / 2

        left, top, right, bottom = _expand_crop_box(left, top, right, bottom, img_w, img_h)
        if left <= 0 and top <= 0 and right >= img_w and bottom >= img_h:
            return img_bytes, width, height, map_view

        cropped = image.crop((left, top, right, bottom))
        crop_w, crop_h = cropped.size
        crop_cx = left + crop_w / 2
        crop_cy = top + crop_h / 2
        center_lat, center_lng = _static_map_latlng(
            crop_cx, crop_cy,
            map_view['center_lat'], map_view['center_lng'],
            map_view['zoom'], width, height,
        )

        out = io.BytesIO()
        cropped.save(out, format='PNG')
        cropped_view = dict(map_view)
        cropped_view.update({
            'center_lat': center_lat,
            'center_lng': center_lng,
            'crop_box_px': [left, top, right, bottom],
            'cropped': True,
        })
        return out.getvalue(), crop_w, crop_h, cropped_view
    except Exception:
        return img_bytes, width, height, map_view


def _image_has_black_tile_region(img_bytes: bytes) -> bool:
    """Detect large black Static Maps tile gaps, not normal roof shadows."""
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(img_bytes)).convert('RGB').resize((80, 80))
        width, height = image.size
        regions = [
            (int(width * 0.60), 0, width, height),
            (0, int(height * 0.60), width, height),
        ]
        for left, top, right, bottom in regions:
            pixels = list(image.crop((left, top, right, bottom)).getdata())
            if not pixels:
                continue
            blackish = sum(1 for r, g, b in pixels if r < 8 and g < 8 and b < 8)
            if blackish / len(pixels) > 0.55:
                return True
    except Exception:
        return False
    return False


def _clean_pct_polygon(poly: list) -> list:
    cleaned = []
    if not isinstance(poly, list):
        return cleaned
    for p in poly:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        try:
            x = max(0.0, min(100.0, float(p[0])))
            y = max(0.0, min(100.0, float(p[1])))
        except (TypeError, ValueError):
            continue
        cleaned.append([round(x, 2), round(y, 2)])
    return cleaned if len(cleaned) >= 3 else []


def _pct_polygon_to_geo(poly_pct: list, map_view: dict, width: int, height: int) -> list:
    geo = []
    for x_pct, y_pct in _clean_pct_polygon(poly_pct):
        lat, lng = _static_map_latlng(
            x_pct / 100 * width,
            y_pct / 100 * height,
            map_view['center_lat'], map_view['center_lng'],
            map_view['zoom'], width, height,
        )
        geo.append([round(lat, 7), round(lng, 7)])
    return geo


def _footprint_image_polygons(map_view: dict, width: int, height: int) -> tuple[list, list]:
    footprint = map_view.get('footprint') or []
    if len(footprint) < 3:
        return [], []

    px = [
        _static_map_pixel(
            p[0], p[1],
            map_view['center_lat'], map_view['center_lng'],
            map_view['zoom'], width, height,
        )
        for p in footprint
    ]
    pct = [[round(x / width * 100, 2), round(y / height * 100, 2)] for x, y in px]
    return px, pct


def _annotate_image_for_roof_vision(img_bytes: bytes, footprint_px: list,
                                    click_px: tuple | None = None) -> tuple[str, str]:
    """
    Claude sees this annotated image, but the UI still receives the raw satellite
    image. The Microsoft footprint is shown as an approximate guide only.
    """
    try:
        from PIL import Image, ImageDraw

        base = Image.open(io.BytesIO(img_bytes)).convert('RGBA')
        annotated = base.copy()
        draw = ImageDraw.Draw(annotated)
        if len(footprint_px) >= 3:
            pts = [(float(x), float(y)) for x, y in footprint_px]
            draw.line(pts + [pts[0]], fill=(0, 255, 150, 230), width=6)
            draw.line(pts + [pts[0]], fill=(255, 255, 255, 220), width=2)
        if click_px:
            cx, cy = click_px
            r = 11
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 210, 0, 245),
                         outline=(20, 20, 20, 255), width=3)

        out = io.BytesIO()
        annotated.convert('RGB').save(out, format='PNG')
        return base64.b64encode(out.getvalue()).decode('utf-8'), 'image/png'
    except Exception:
        return base64.b64encode(img_bytes).decode('utf-8'), 'image/png'


def _point_in_poly_xy(x: float, y: float, poly: list) -> bool:
    inside = False
    n = len(poly)
    if n < 3:
        return True
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _filter_sections_to_footprint(parsed: dict, footprint_pct: list) -> int:
    """Filter sections whose centroid lies outside footprint_pct.

    Safety rule: if filtering would discard ALL sections, keep every section
    instead — the footprint boundary is probably wrong or too tight, and
    showing Claude's output (even if slightly outside) is better than nothing.
    """
    if len(footprint_pct) < 3:
        return 0

    kept = []
    dropped_secs = []
    for sec in parsed.get('sections', []) or []:
        poly = sec.get('polygon') or []
        if len(poly) < 3:
            dropped_secs.append(sec)
            continue
        cx = sum(float(p[0]) for p in poly) / len(poly)
        cy = sum(float(p[1]) for p in poly) / len(poly)
        if _point_in_poly_xy(cx, cy, footprint_pct):
            kept.append(sec)
        else:
            dropped_secs.append(sec)

    dropped = len(dropped_secs)

    if not kept:
        # Filtering would leave nothing — footprint is likely wrong; keep all
        note = parsed.get('notes', '')
        parsed['notes'] = (note + " Footprint boundary could not be used for section filtering — showing all detected sections.").strip()
        return 0

    parsed['sections'] = kept
    if dropped:
        note = parsed.get('notes', '')
        extra = f"{dropped} section(s) outside the selected footprint were discarded."
        parsed['notes'] = f"{note} {extra}".strip()
    return dropped


def _apply_solar_to_sections(sections: list, solar_sections: list) -> None:
    """
    In-place: for each Claude-drawn section, find the best-matching Solar API
    section by facing direction and update pitch_est + area_m2 with Solar values.
    Each solar section can only be consumed once (greedy, largest area first).
    """
    if not sections or not solar_sections:
        return
    remaining = list(solar_sections)
    for sec in sections:
        facing = (sec.get('facing') or '').upper()
        best = None
        best_i = -1
        for i, ss in enumerate(remaining):
            if ss.get('facing', '').upper() == facing:
                if best is None or ss['area_m2'] > best['area_m2']:
                    best = ss
                    best_i = i
        if best is not None:
            sec['pitch_est'] = best['pitch_deg']
            sec['area_m2'] = round(best['area_m2'], 1)
            sec['solar_calibrated'] = True
            remaining.pop(best_i)


def _fetch_context_zoom_image(
    lat: float, lng: float, zoom: int, width: int, height: int, api_key: str,
) -> bytes | None:
    """Fetch a Google Static Maps satellite image at *zoom* centred on lat/lng.

    Uses the short-TTL cache.  Returns raw bytes or None on failure.
    The context image is NOT cropped — it shows a wider neighbourhood view.
    """
    cache_payload = {
        'center_lat': round(lat, 7),
        'center_lng': round(lng, 7),
        'zoom': zoom,
        'size': f'{width}x{height}',
        'maptype': 'satellite',
        'version': 'context-zoom-v1',
    }
    cached = get_cached('google_static_maps_satellite', cache_payload)
    if cached:
        return cached.get('img_bytes') or None
    params = urllib.parse.urlencode({
        'center': f'{lat},{lng}',
        'zoom': zoom,
        'size': f'{width}x{height}',
        'maptype': 'satellite',
        'key': api_key,
    })
    url = f'https://maps.googleapis.com/maps/api/staticmap?{params}'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'aequilibri-poc/1.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            img_bytes = resp.read()
        set_cached('google_static_maps_satellite', cache_payload, {
            'img_bytes': img_bytes, 'content_type': 'image/png', 'zoom': zoom,
        }, SHORT_TTL_SECONDS)
        return img_bytes
    except Exception:
        return None


def _crop_image_to_outline_pct(
    img_bytes: bytes,
    outline_pct: list,
    full_w: int,
    full_h: int,
    padding_pct: float = 8.0,
) -> tuple[bytes, dict]:
    """Crop *img_bytes* to the bounding box of *outline_pct* with padding.

    *outline_pct* vertices are percentage coordinates of the full image.
    Returns ``(cropped_bytes, crop_info)`` where crop_info keys are:
      offset_x_pct, offset_y_pct — top-left of crop in full-image %
      crop_w_pct,   crop_h_pct   — dimensions in full-image %
      crop_w_px,    crop_h_px    — pixel dimensions of the returned crop

    Returns ``(img_bytes, {})`` on failure.
    """
    if len(outline_pct) < 3:
        return img_bytes, {}
    try:
        from PIL import Image as _PILImage
        xs = [float(p[0]) for p in outline_pct]
        ys = [float(p[1]) for p in outline_pct]
        x_min = max(0.0,   min(xs) - padding_pct)
        x_max = min(100.0, max(xs) + padding_pct)
        y_min = max(0.0,   min(ys) - padding_pct)
        y_max = min(100.0, max(ys) + padding_pct)
        px_l = int(x_min / 100 * full_w)
        px_r = int(x_max / 100 * full_w)
        px_t = int(y_min / 100 * full_h)
        px_b = int(y_max / 100 * full_h)
        if px_r - px_l < 60 or px_b - px_t < 60:
            return img_bytes, {}
        img = _PILImage.open(io.BytesIO(img_bytes)).convert('RGB')
        cropped = img.crop((px_l, px_t, px_r, px_b))
        cw, ch = cropped.size
        out = io.BytesIO()
        cropped.save(out, format='PNG')
        return out.getvalue(), {
            'offset_x_pct': x_min, 'offset_y_pct': y_min,
            'crop_w_pct': x_max - x_min, 'crop_h_pct': y_max - y_min,
            'crop_w_px': cw, 'crop_h_px': ch,
        }
    except Exception:
        return img_bytes, {}


def _remap_pct_polygon(polygon: list, crop_info: dict) -> list:
    """Convert polygon % coords from the cropped image back to full-image % coords."""
    if not crop_info or not polygon:
        return polygon
    ox = crop_info.get('offset_x_pct', 0.0)
    oy = crop_info.get('offset_y_pct', 0.0)
    cw = crop_info.get('crop_w_pct', 100.0)
    ch = crop_info.get('crop_h_pct', 100.0)
    return [[round(ox + p[0] / 100 * cw, 2), round(oy + p[1] / 100 * ch, 2)]
            for p in polygon]


def _full_px_to_crop_px(
    px_x: float, px_y: float, full_w: int, full_h: int, crop_info: dict,
) -> tuple[float, float]:
    """Convert a pixel position in the full image to its position inside the crop."""
    ox  = crop_info.get('offset_x_pct', 0.0)
    oy  = crop_info.get('offset_y_pct', 0.0)
    cw_pct = crop_info.get('crop_w_pct', 100.0)
    ch_pct = crop_info.get('crop_h_pct', 100.0)
    cw_px  = crop_info.get('crop_w_px', float(full_w))
    ch_px  = crop_info.get('crop_h_px', float(full_h))
    return (
        (px_x / full_w * 100 - ox) / cw_pct * cw_px,
        (px_y / full_h * 100 - oy) / ch_pct * ch_px,
    )


def _merge_weak_sections(parsed: dict, max_sections: int = 8) -> None:
    """In-place: when confidence is 'low' and section count > max_sections,
    merge the smallest section (by area) into its nearest neighbour (centroid
    distance) until the count reaches max_sections.

    Only area is combined — the larger section's geometry is kept so the drawn
    outline stays clean.  Must be called *after* area_m2 has been assigned.
    """
    sections = parsed.get('sections') or []
    if parsed.get('confidence') != 'low' or len(sections) <= max_sections:
        return

    def _centroid(sec: dict) -> tuple[float, float]:
        poly = sec.get('polygon') or []
        if not poly:
            return (50.0, 50.0)
        return (sum(p[0] for p in poly) / len(poly),
                sum(p[1] for p in poly) / len(poly))

    merged_count = 0
    while len(sections) > max_sections:
        smallest_i = min(range(len(sections)),
                         key=lambda i: sections[i].get('area_m2') or 0)
        cx, cy = _centroid(sections[smallest_i])
        best_j, best_dist = -1, float('inf')
        for j, sec in enumerate(sections):
            if j == smallest_i:
                continue
            sx, sy = _centroid(sec)
            d = math.sqrt((cx - sx) ** 2 + (cy - sy) ** 2)
            if d < best_dist:
                best_dist, best_j = d, j
        if best_j < 0:
            break
        absorbed = sections.pop(smallest_i)
        # Adjust target index after pop
        target_j = best_j if best_j < smallest_i else best_j - 1
        if 0 <= target_j < len(sections):
            nb = sections[target_j]
            nb['area_m2'] = round(
                (nb.get('area_m2') or 0) + (absorbed.get('area_m2') or 0), 1
            )
            lbl = absorbed.get('label') or f"S{absorbed.get('id', '')}"
            nb['notes'] = ((nb.get('notes') or '') + f' +{lbl}').strip()
        merged_count += 1

    if merged_count:
        note = parsed.get('notes', '')
        parsed['notes'] = (
            f"{note} [{merged_count} section(s) auto-merged — low confidence]"
        ).strip()


@csrf_exempt
def roof_drawing_analyze(request):
    """
    POST /uc1/api/roof-drawing/
    Body (JSON): { lat, lng, zoom? }

    1. Fetches a satellite screenshot from Google Static Maps API
    2. Sends to Claude Vision for roof section detection
    3. Returns: { image_b64, sections, scale, roof_type, confidence, notes }
    """
    from core.claude_client import call_claude_vision

    try:
        body    = json.loads(request.body)
        lat     = float(body['lat'])
        lng     = float(body['lng'])
        requested_zoom = int(body.get('zoom', 20))
        max_zoom       = int(body.get('max_zoom', 21))
        known_roof_area_m2 = float(body.get('known_roof_area_m2') or 0)
        known_ground_area_m2 = float(body.get('known_ground_area_m2') or 0)
        use_ms_guide = bool(body.get('use_ms_guide', False))
        focus_polygon = _clean_geo_polygon_latlng(body.get('focus_polygon') or [])
        width   = 640
        height  = 640
        map_view = _roof_static_map_view(
            lat, lng, width, height,
            fallback_zoom=requested_zoom,
            max_zoom=max_zoom,
            known_roof_area_m2=known_roof_area_m2,
            known_ground_area_m2=known_ground_area_m2,
            use_ms_guide=use_ms_guide,
            focus_polygon=focus_polygon,
        )
        zoom = map_view['zoom']
        map_center_lat = map_view['center_lat']
        map_center_lng = map_view['center_lng']

        # ── Fetch satellite image ────────────────────────────────────────────
        api_key = (getattr(settings, 'GOOGLE_API_KEY', '') or
                   getattr(settings, 'GOOGLE_MAPS_API_KEY', '') or
                   getattr(settings, 'GOOGLE_SOLAR_API_KEY', ''))
        if not api_key:
            return JsonResponse({'ok': False, 'error': 'No Google API key configured'}, status=500)

        params = urllib.parse.urlencode({
            'center':  f'{map_center_lat},{map_center_lng}',
            'zoom':    zoom,
            'size':    f'{width}x{height}',
            'maptype': 'satellite',
            'key':     api_key,
        })
        maps_url = f'https://maps.googleapis.com/maps/api/staticmap?{params}'

        try:
            static_cache_payload = {
                'center_lat': round(map_center_lat, 7),
                'center_lng': round(map_center_lng, 7),
                'zoom': zoom,
                'size': f'{width}x{height}',
                'maptype': 'satellite',
                'version': 'black-tile-retry-v1',
            }
            cached_static = get_cached('google_static_maps_satellite', static_cache_payload)
            if cached_static:
                img_bytes = cached_static.get('img_bytes') or b''
                content_type = cached_static.get('content_type') or 'image/png'
                cached_zoom = cached_static.get('zoom')
                if cached_zoom:
                    zoom = int(cached_zoom)
                    map_view = dict(map_view)
                    map_view['zoom'] = zoom
                    map_view['meters_per_px'] = (
                        156543.03392 * math.cos(math.radians(map_center_lat)) / (2 ** zoom)
                    )
            else:
                req = urllib.request.Request(maps_url, headers={'User-Agent': 'aequilibri-poc/1.0'})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    img_bytes = resp.read()
                    content_type = resp.getheader('Content-Type', 'image/png')
                if _image_has_black_tile_region(img_bytes) and zoom > 17:
                    try:
                        retry_zoom = zoom - 1
                        retry_params = urllib.parse.urlencode({
                            'center':  f'{map_center_lat},{map_center_lng}',
                            'zoom':    retry_zoom,
                            'size':    f'{width}x{height}',
                            'maptype': 'satellite',
                            'key':     api_key,
                        })
                        retry_url = f'https://maps.googleapis.com/maps/api/staticmap?{retry_params}'
                        retry_req = urllib.request.Request(retry_url, headers={'User-Agent': 'aequilibri-poc/1.0'})
                        with urllib.request.urlopen(retry_req, timeout=15) as retry_resp:
                            retry_bytes = retry_resp.read()
                            if not _image_has_black_tile_region(retry_bytes):
                                img_bytes = retry_bytes
                                content_type = retry_resp.getheader('Content-Type', 'image/png')
                                zoom = retry_zoom
                                map_view = dict(map_view)
                                map_view['zoom'] = zoom
                                map_view['meters_per_px'] = (
                                    156543.03392 * math.cos(math.radians(map_center_lat)) / (2 ** zoom)
                                )
                    except Exception:
                        pass
                set_cached('google_static_maps_satellite', static_cache_payload, {
                    'img_bytes': img_bytes,
                    'content_type': content_type,
                    'zoom': zoom,
                }, SHORT_TTL_SECONDS)
            if not img_bytes:
                return JsonResponse({'ok': False, 'error': 'Static Maps returned no image'}, status=502)
        except Exception as e:
            return JsonResponse({'ok': False, 'error': f'Static Maps fetch failed: {e}'}, status=502)

        img_bytes, width, height, map_view = _crop_static_map_to_selected_roof(
            img_bytes, map_view, lat, lng, width, height,
        )
        content_type = 'image/png'
        map_center_lat = map_view['center_lat']
        map_center_lng = map_view['center_lng']
        zoom = map_view['zoom']

        img_b64 = base64.b64encode(img_bytes).decode('utf-8')
        media_type = content_type.split(';')[0].strip() or 'image/png'
        footprint_px, footprint_pct = _footprint_image_polygons(map_view, width, height)
        click_px = _static_map_pixel(lat, lng, map_center_lat, map_center_lng, zoom, width, height)
        vision_b64, vision_media_type = _annotate_image_for_roof_vision(img_bytes, footprint_px, click_px)

        # ── Call Claude Vision ───────────────────────────────────────────────
        guide_source = map_view.get('footprint_source') or ''
        if footprint_pct and guide_source == 'selected_outline':
            guide_prompt = (
                f"The current map roof guide is shown in green and has this "
                f"percentage-coordinate polygon: {json.dumps(footprint_pct)}. Use it to locate and crop the target roof, "
                f"but do not copy it as the final outline if visible roof edges disagree. "
            )
        elif footprint_pct:
            guide_prompt = (
                f"The approximate Microsoft footprint is outlined in green and has this "
                f"percentage-coordinate polygon: {json.dumps(footprint_pct)}. Use it only as a hint. "
            )
        else:
            guide_prompt = (
                "No external footprint guide is provided. Use only the yellow clicked point and image evidence. "
            )
        correction_learning = roof_correction_learning_prompt(limit=8)
        correction_learning_text = (
            f"Recent saved correction learning:\n{correction_learning.text}\n"
            if correction_learning.text else ""
        )

        # ── Solar API: fetch dominant pitch + total area for calibration only ──
        # IMPORTANT: Do NOT feed individual Solar API sections to Claude.
        # The Solar API covers ALL structures at the location (entire farm/complex),
        # so listing its sections causes Claude to draw every building, not just the
        # clicked one.  Only the dominant pitch and total area are safe calibration hints.
        solar_data: dict = {}
        solar_context = ""
        try:
            from uc1_roofing.solar_api_service import full_solar_analysis as _solar_full
            _s = _solar_full(lat, lng)
            if _s.get("ok") and _s.get("sections"):
                solar_data = _s
                dominant_pitch = _s.get("dominant_pitch_deg")
                total_area = _s.get("total_area_m2")
                imagery_date = _s.get("imagery_date", "?")
                solar_context = (
                    f"\n\nCalibration reference (Google Solar API, imagery {imagery_date}): "
                    f"the dominant roof pitch at this location is approximately "
                    f"{dominant_pitch}°"
                    + (f" and the clicked building's total roof area is approximately {total_area} m²"
                       if total_area else "")
                    + ". Use the pitch when assigning pitch_est values. "
                    "Do NOT use this to decide how many sections to draw — "
                    "draw only the sections you can visually observe on the single roof "
                    "containing the yellow dot."
                )
        except Exception:
            pass

        # ── Suburb pattern learning (added to the single-pass prompt) ───────
        # This is the ONE blue-sky improvement we keep; it injects a plausibility
        # hint without altering image inputs or coordinate spaces.
        address_str = str(body.get('address') or '')
        suburb_hint = ''
        if address_str:
            suburb_key = extract_suburb(address_str)
            if suburb_key:
                sp = suburb_section_pattern(suburb_key)
                if sp:
                    suburb_hint = f'\n{sp.prompt_text}'

        user_prompt = (
            f"Analyze this tightly cropped satellite image of the selected roof. "
            f"The clicked coordinates are ({lat:.5f}, {lng:.5f}); the image center is "
            f"({map_center_lat:.5f}, {map_center_lng:.5f}) at zoom {zoom}. "
            f"Image size: {width}x{height} pixels. "
            f"{guide_prompt}"
            f"{correction_learning_text}"
            f"The yellow dot marks the user's click on the selected roof. "
            f"The yellow dot marks the SINGLE roof to analyse. Return roof_outline tightly around "
            f"only that one connected structure. If multiple buildings are visible, "
            f"draw ONLY the building where the yellow dot sits — exclude every other structure. "
            f"Use roof_outline as the boundary for all sections. "
            f"Before returning JSON, internally verify: (1) the yellow dot is inside roof_outline, "
            f"(2) all section polygons sit inside roof_outline, (3) no other building is included, "
            f"(4) every section boundary aligns with a visible ridge line, hip ridge, or valley line — "
            f"if no such line exists, the two surfaces are ONE section not two. "
            f"{solar_context}{suburb_hint}"
            f"Return the JSON as instructed."
        )

        # ── Single-pass Claude call (Opus 4.7) ───────────────────────────────
        # Earlier multi-pass / multi-zoom code was REMOVED because the pass-2
        # crop-and-retrace step produced a looser roof_outline than the
        # original single-pass call (the cropped image gave Claude too much
        # padding around the roof and it traced the crop boundary instead of
        # the roof edge). Reverted to the original single-pass flow.
        multi_pass_used = False
        result = call_claude_vision(ROOF_VISION_SYSTEM, user_prompt, vision_b64, vision_media_type)

        # ── Parse Claude's JSON response ─────────────────────────────────────
        raw = result['content'].strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"sections": [], "roof_type": "unknown", "confidence": "low",
                      "notes": "Could not parse Claude response"}

        ai_outline_pct = _clean_pct_polygon(parsed.get('roof_outline') or [])
        ai_footprint = _pct_polygon_to_geo(ai_outline_pct, map_view, width, height)
        # Use AI outline as filter boundary when available; if not, use building
        # footprint but only if it has a reasonable number of vertices (3+).
        # Never use a boundary that's smaller than a plausible roof polygon.
        section_boundary_pct = ai_outline_pct if len(ai_outline_pct) >= 3 else footprint_pct
        dropped_sections = _filter_sections_to_footprint(parsed, section_boundary_pct)

        # ── Attach colors + area estimates ───────────────────────────────────
        # Scale: meters per pixel at the actual Static Maps center/zoom.
        meters_per_px = map_view['meters_per_px']

        for sec in parsed.get('sections', []):
            sec['color'] = FACING_COLORS_PY.get(sec.get('facing', ''), '#607D8B')
            # Estimate area from polygon (shoelace, in % coords → scale to m²)
            poly = sec.get('polygon', [])
            if len(poly) >= 3:
                px_poly = [(p[0] / 100 * width, p[1] / 100 * height) for p in poly]
                # Shoelace formula in pixels
                n = len(px_poly)
                area_px2 = abs(sum(
                    px_poly[i][0] * px_poly[(i+1) % n][1] -
                    px_poly[(i+1) % n][0] * px_poly[i][1]
                    for i in range(n)
                )) / 2
                # Convert to m², apply pitch factor
                pitch_rad = math.radians(sec.get('pitch_est', 20))
                slope_factor = 1 / math.cos(pitch_rad) if pitch_rad < math.pi / 2 else 1
                sec['area_m2'] = round(area_px2 * meters_per_px ** 2 * slope_factor, 1)
            else:
                sec['area_m2'] = 0

        # ── Confidence-weighted section merging ───────────────────────────────
        # Must run AFTER area_m2 is assigned above.
        _merge_weak_sections(parsed, max_sections=8)

        return JsonResponse({
            'ok': True,
            'image_b64': img_b64,
            'media_type': media_type,
            'width': width,
            'height': height,
            'center': {
                'lat': round(map_center_lat, 7),
                'lng': round(map_center_lng, 7),
            },
            'requested_point': {
                'lat': round(lat, 7),
                'lng': round(lng, 7),
            },
            'footprint': map_view.get('footprint', []),
            'footprint_pct': footprint_pct,
            'ai_footprint': ai_footprint,
            'ai_outline_pct': ai_outline_pct,
            'footprint_source': map_view.get('footprint_source', ''),
            'crop_box_px': map_view.get('crop_box_px', []),
            'cropped': bool(map_view.get('cropped', False)),
            'static_map_version': 'roof-crop-v4',
            'guide_area_sqm': round(map_view.get('guide_area_sqm') or 0, 1),
            'target_span_m': round(map_view.get('target_span_m') or 0, 1),
            'dropped_sections': dropped_sections,
            'scale': {
                'meters_per_px': round(meters_per_px, 4),
                'zoom': zoom,
            },
            'sections': parsed.get('sections', []),
            'roof_type': parsed.get('roof_type', 'unknown'),
            'confidence': parsed.get('confidence', 'low'),
            'notes': parsed.get('notes', ''),
            'demo_mode': result.get('demo_mode', False),
            'multi_pass_used': multi_pass_used,
            'correction_learning_applied': bool(correction_learning.text),
            'correction_learning_count': correction_learning.correction_count,
            # Solar API calibration data
            'solar_used': bool(solar_data.get('ok')),
            'solar_sections': solar_data.get('sections', []),
            'solar_total_area_m2': solar_data.get('total_area_m2'),
            'solar_dominant_pitch_deg': solar_data.get('dominant_pitch_deg'),
            'solar_imagery_date': solar_data.get('imagery_date'),
            'solar_imagery_quality': solar_data.get('imagery_quality'),
        })

    except (KeyError, ValueError, json.JSONDecodeError) as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)


# ─── AJAX: Area Preview ───────────────────────────────────────────────────────

def _roof_correction_address_key(value):
    return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())


def _roof_correction_float(value):
    try:
        if value in (None, ''):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _roof_correction_distance_m(lat1, lng1, lat2, lng2):
    if None in (lat1, lng1, lat2, lng2):
        return None
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _roof_correction_payload(log):
    try:
        payload = json.loads(log.payload or '{}')
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@csrf_exempt
def roof_correction_save(request):
    """
    Persist a verified AI roof drawing correction as audit/knowledge data.

    The existing ExecutionLog gives us append-only correction memory without a
    database migration while this workflow is still evolving.
    """
    if request.method == 'GET':
        address = str(request.GET.get('address') or '')[:300]
        lat = _roof_correction_float(request.GET.get('lat'))
        lng = _roof_correction_float(request.GET.get('lng'))
        address_key = _roof_correction_address_key(address)
        best = None
        best_score = 0
        best_distance = None

        logs = ExecutionLog.objects.filter(
            tool_name='roof_correction',
            status='success',
        ).order_by('-created_at')[:300]

        for log in logs:
            payload = _roof_correction_payload(log)
            if not payload:
                continue

            score = 0
            candidate_key = _roof_correction_address_key(payload.get('address'))
            if address_key and candidate_key:
                if address_key == candidate_key:
                    score += 1000
                elif len(address_key) > 10 and (address_key in candidate_key or candidate_key in address_key):
                    score += 850

            cand_lat = _roof_correction_float(payload.get('lat'))
            cand_lng = _roof_correction_float(payload.get('lng'))
            distance = _roof_correction_distance_m(lat, lng, cand_lat, cand_lng)
            if distance is not None:
                if distance <= 8:
                    score += 900
                elif distance <= 30:
                    score += 750
                elif distance <= 75:
                    score += 350

            if score > best_score:
                best = (log, payload)
                best_score = score
                best_distance = distance

        if not best or best_score < 500:
            return JsonResponse({'ok': True, 'found': False})

        log, payload = best
        return JsonResponse({
            'ok': True,
            'found': True,
            'id': log.id,
            'created_at': log.created_at.isoformat(),
            'match_score': best_score,
            'distance_m': round(best_distance, 2) if best_distance is not None else None,
            'correction': payload,
        })

    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Unsupported method'}, status=405)

    try:
        body = json.loads(request.body)
        address = str(body.get('address') or '')[:300]
        sections = body.get('sections') or []
        footprint = body.get('footprint') or []
        quality = body.get('quality') or {}
        payload = {
            'address': address,
            'lat': body.get('lat'),
            'lng': body.get('lng'),
            'footprint': footprint,
            'drawing_boundary_pct': body.get('drawing_boundary_pct') or [],
            'sections': sections,
            'quality': quality,
            'footprint_area_m2': body.get('footprint_area_m2'),
            'total_area_m2': body.get('total_area_m2'),
            'perimeter_m': body.get('perimeter_m'),
            'avg_pitch_deg': body.get('avg_pitch_deg'),
            'source': body.get('source') or 'ai_roof_drawing',
            'notes': body.get('notes') or '',
        }
        log = ExecutionLog.objects.create(
            tool_name='roof_correction',
            payload=json.dumps(payload),
            result=json.dumps({
                'ok': True,
                'address': address,
                'section_count': len(sections) if isinstance(sections, list) else 0,
                'outline_vertices': len(footprint) if isinstance(footprint, list) else 0,
                'quality_level': quality.get('level') if isinstance(quality, dict) else '',
            }),
            status='success',
        )
        return JsonResponse({'ok': True, 'id': log.id})
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)


@csrf_exempt
def manual_ground_truth_save(request):
    """
    Store and retrieve Peter/manual measurement records for sprint evidence.

    These records are intentionally append-only while the sprint workflow is
    evolving. They let the UI compare AI measurements against confirmed manual
    figures without adding a migration yet.
    """
    if request.method == 'GET':
        address = str(request.GET.get('address') or '')[:300]
        lat = _roof_correction_float(request.GET.get('lat'))
        lng = _roof_correction_float(request.GET.get('lng'))
        address_key = _roof_correction_address_key(address)
        best = None
        best_score = 0
        best_distance = None

        logs = ExecutionLog.objects.filter(
            tool_name='manual_ground_truth',
            status='success',
        ).order_by('-created_at')[:300]

        for log in logs:
            payload = _roof_correction_payload(log)
            if not payload:
                continue

            score = 0
            candidate_key = _roof_correction_address_key(payload.get('address'))
            if address_key and candidate_key:
                if address_key == candidate_key:
                    score += 1000
                elif len(address_key) > 10 and (address_key in candidate_key or candidate_key in address_key):
                    score += 850

            cand_lat = _roof_correction_float(payload.get('lat'))
            cand_lng = _roof_correction_float(payload.get('lng'))
            distance = _roof_correction_distance_m(lat, lng, cand_lat, cand_lng)
            if distance is not None:
                if distance <= 8:
                    score += 900
                elif distance <= 30:
                    score += 750
                elif distance <= 75:
                    score += 350

            if score > best_score:
                best = (log, payload)
                best_score = score
                best_distance = distance

        if not best or best_score < 500:
            return JsonResponse({'ok': True, 'found': False})

        log, payload = best
        return JsonResponse({
            'ok': True,
            'found': True,
            'id': log.id,
            'created_at': log.created_at.isoformat(),
            'match_score': best_score,
            'distance_m': round(best_distance, 2) if best_distance is not None else None,
            'ground_truth': payload,
        })

    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Unsupported method'}, status=405)

    try:
        body = json.loads(request.body)
        fields = body.get('fields') or {}
        if not isinstance(fields, dict):
            fields = {}
        address = str(body.get('address') or '')[:300]
        payload = {
            'address': address,
            'lat': body.get('lat'),
            'lng': body.get('lng'),
            'sample_id': body.get('sample_id') or '',
            'source': body.get('source') or 'peter_manual_sheet',
            'fields': fields,
            'raw_measurements': body.get('raw_measurements') or '',
            'notes': body.get('notes') or '',
        }
        log = ExecutionLog.objects.create(
            tool_name='manual_ground_truth',
            payload=json.dumps(payload),
            result=json.dumps({
                'ok': True,
                'address': address,
                'field_count': len([v for v in fields.values() if v not in ('', None)]),
            }),
            status='success',
        )
        return JsonResponse({'ok': True, 'id': log.id})
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)


def area_preview(request):
    """Return adjusted area given flat_area, pitch, waste."""
    try:
        flat_area    = float(request.GET.get('flat_area', 0))
        pitch_type   = request.GET.get('pitch_type', 'standard')
        waste_factor = float(request.GET.get('waste_factor', 10))
        pitch_factor = PITCH_FACTORS.get(pitch_type, 1.0)
        adjusted     = round(flat_area * pitch_factor * (1 + waste_factor / 100), 2)
        return JsonResponse({'adjusted_area': adjusted, 'pitch_factor': pitch_factor})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 1 — Guttering Auto-Quote
# ═══════════════════════════════════════════════════════════════════════════════

def guttering_rates(request):
    qs = GutteringRate.objects.all()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create':
            GutteringRate.objects.create(
                item_type   = request.POST.get('item_type'),
                description = request.POST.get('description', ''),
                unit        = request.POST.get('unit', 'lm'),
                rate_ex_gst = request.POST.get('rate_ex_gst'),
            )
            messages.success(request, 'Guttering rate added.')
        elif action == 'delete':
            GutteringRate.objects.filter(pk=request.POST.get('rate_id')).delete()
            messages.success(request, 'Rate deleted.')
        elif action == 'toggle':
            r = get_object_or_404(GutteringRate, pk=request.POST.get('rate_id'))
            r.is_active = not r.is_active
            r.save()
        return redirect('uc1:guttering_rates')
    return render(request, 'uc1_roofing/guttering_rates.html', {
        'rates': qs, 'item_type_choices': GutteringRate._meta.get_field('item_type').choices,
    })


def auto_add_guttering(request, pk):
    """Auto-calculate guttering from LiDAR data and add to quote as line items."""
    quote = get_object_or_404(Quote, pk=pk)

    # Check for existing guttering items
    existing = quote.items.filter(description__icontains='gutter').count()
    if existing > 0:
        messages.warning(request, 'Guttering items already exist on this quote. Remove them first.')
        return redirect('uc1:quote_detail', pk=pk)

    # Require LiDAR data
    try:
        lidar = quote.lidar_analysis
    except Exception:
        messages.error(request, 'No LiDAR data for this quote. Run the Roof Inspector first.')
        return redirect('uc1:quote_detail', pk=pk)

    perimeter = lidar.perimeter_m
    if not perimeter:
        messages.error(request, 'LiDAR data has no perimeter measurement.')
        return redirect('uc1:quote_detail', pk=pk)

    rates = GutteringRate.objects.filter(is_active=True)
    if not rates.exists():
        messages.warning(request, 'No guttering rates configured. Set rates in Guttering Rates first.')
        return redirect('uc1:guttering_rates')

    added = 0

    gutter_r = rates.filter(item_type='gutter').first()
    if gutter_r:
        QuoteItem.objects.create(
            quote=quote, description=f'Guttering — {gutter_r.description}',
            quantity=round(perimeter, 1), unit='lm',
            unit_price_ex_gst=gutter_r.rate_ex_gst, sort_order=100,
        )
        added += 1

    downpipe_r = rates.filter(item_type='downpipe').first()
    if downpipe_r:
        count = max(2, math.ceil(perimeter / 15))
        QuoteItem.objects.create(
            quote=quote, description=f'Downpipes — {downpipe_r.description}',
            quantity=count, unit='each',
            unit_price_ex_gst=downpipe_r.rate_ex_gst, sort_order=101,
        )
        added += 1

    valley_r = rates.filter(item_type='valley').first()
    if valley_r:
        QuoteItem.objects.create(
            quote=quote, description=f'Valley Iron — {valley_r.description}',
            quantity=round(perimeter * 0.20, 1), unit='lm',
            unit_price_ex_gst=valley_r.rate_ex_gst, sort_order=102,
        )
        added += 1

    ridge_r = rates.filter(item_type='ridge_cap').first()
    if ridge_r:
        QuoteItem.objects.create(
            quote=quote, description=f'Ridge Cap — {ridge_r.description}',
            quantity=round(perimeter * 0.30, 1), unit='lm',
            unit_price_ex_gst=ridge_r.rate_ex_gst, sort_order=103,
        )
        added += 1

    ExecutionLog.objects.create(
        tool_name='auto_guttering',
        payload=json.dumps({'perimeter_m': perimeter, 'items': added}),
        result='{"status":"success"}', status='success', quote=quote,
    )
    messages.success(request,
        f'✅ Added {added} guttering items based on {perimeter:.0f}m perimeter from LiDAR data.')
    return redirect('uc1:quote_detail', pk=pk)


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 2 — Solar Bundle
# ═══════════════════════════════════════════════════════════════════════════════

def solar_partners(request):
    partners = SolarPartner.objects.all()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create':
            SolarPartner.objects.create(
                name=request.POST.get('name', ''),
                contact_name=request.POST.get('contact_name', ''),
                contact_email=request.POST.get('contact_email', ''),
                contact_phone=request.POST.get('contact_phone', ''),
                referral_fee_pct=request.POST.get('referral_fee_pct', 10),
                avg_install_value=request.POST.get('avg_install_value', 10000),
                notes=request.POST.get('notes', ''),
            )
            messages.success(request, 'Solar partner added.')
        elif action == 'toggle':
            p = get_object_or_404(SolarPartner, pk=request.POST.get('partner_id'))
            p.is_active = not p.is_active
            p.save()
        elif action == 'delete':
            SolarPartner.objects.filter(pk=request.POST.get('partner_id')).delete()
        return redirect('uc1:solar_partners')
    return render(request, 'uc1_roofing/solar_partners.html', {'partners': partners})


def solar_bundle(request, pk):
    """Solar opportunity analysis + referral submission for a quote."""
    from core.claude_client import call_claude as _call_claude
    quote            = get_object_or_404(Quote, pk=pk)
    partners         = SolarPartner.objects.filter(is_active=True)
    existing_referral = quote.solar_referrals.first()

    # Parse roof sections from Google Solar API data
    solar_sections = []
    best_section   = None
    total_kwh      = 0.0
    total_cap_kw   = 0.0

    if quote.roof_sections_json:
        try:
            raw = json.loads(quote.roof_sections_json)
            for s in raw:
                area   = float(s.get('area', 0))
                sun_h  = float(s.get('sun_hours', s.get('sunshine_hours', 0)))
                kwh    = round(sun_h * area / 1000, 0)
                cap_kw = round(area / 6.5, 1)
                solar_sections.append({**s, 'annual_kwh': kwh, 'capacity_kw': cap_kw})
                total_kwh  += kwh
                total_cap_kw += cap_kw
            if solar_sections:
                best_section = max(solar_sections, key=lambda x: x.get('annual_kwh', 0))
        except Exception:
            pass

    total_kwh    = round(total_kwh, 0)
    total_cap_kw = round(total_cap_kw, 1)

    if request.method == 'POST' and not existing_referral:
        partner_id = request.POST.get('partner_id')
        if partner_id:
            partner   = get_object_or_404(SolarPartner, pk=partner_id)
            est_value = round(total_cap_kw * 1500, 2)
            est_fee   = round(float(est_value) * float(partner.referral_fee_pct) / 100, 2)
            ref = SolarReferral.objects.create(
                quote=quote, partner=partner,
                solar_potential_kwh=total_kwh,
                best_section_area=float(best_section.get('area', 0)) if best_section else 0,
                best_section_facing=best_section.get('facing', '') if best_section else '',
                estimated_capacity_kw=total_cap_kw,
                estimated_install_value=est_value,
                estimated_referral_fee=est_fee,
                client_notes=request.POST.get('client_notes', ''),
                status='submitted',
                submitted_at=timezone.now(),
            )
            ExecutionLog.objects.create(
                tool_name='solar_referral_submit',
                payload=json.dumps({'partner': partner.name, 'capacity_kw': total_cap_kw}),
                result=json.dumps({'fee': float(est_fee), 'ref_id': ref.id}),
                status='success', quote=quote,
            )
            messages.success(request,
                f'☀️ Solar referral submitted to {partner.name}. '
                f'Estimated referral fee: ${est_fee:,.2f}')
            return redirect('uc1:solar_bundle', pk=pk)

    return render(request, 'uc1_roofing/solar_bundle.html', {
        'quote': quote, 'partners': partners,
        'solar_sections': solar_sections, 'best_section': best_section,
        'total_kwh': total_kwh, 'total_cap_kw': total_cap_kw,
        'existing_referral': existing_referral,
        'has_solar_data': bool(solar_sections),
        'lidar': getattr(quote, 'lidar_analysis', None),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 3 — Finance Integration
# ═══════════════════════════════════════════════════════════════════════════════

def _seed_finance_providers():
    """Create default providers if none exist."""
    if FinanceProvider.objects.exists():
        return
    defaults = [
        dict(name='Brighte Green Loan', slug='brighte',
             interest_rate_pct=0, min_term_months=12, max_term_months=60,
             min_amount=1000, tagline='0% interest · Fast approval · Green home loan'),
        dict(name='Zip Money', slug='zip',
             interest_rate_pct=19.9, min_term_months=3, max_term_months=36,
             min_amount=500, tagline='Buy now, pay later · 3-month interest-free period'),
        dict(name='Commonwealth Bank HomeStart', slug='commbank',
             interest_rate_pct=6.99, min_term_months=12, max_term_months=84,
             min_amount=5000, tagline='CBA personal loan · fixed rate'),
    ]
    for d in defaults:
        FinanceProvider.objects.create(**d)


def finance_providers(request):
    _seed_finance_providers()
    providers = FinanceProvider.objects.all()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create':
            FinanceProvider.objects.create(
                name=request.POST.get('name', ''),
                slug=request.POST.get('slug', '').lower().replace(' ', '_'),
                interest_rate_pct=request.POST.get('interest_rate_pct', 0),
                min_term_months=request.POST.get('min_term_months', 12),
                max_term_months=request.POST.get('max_term_months', 60),
                min_amount=request.POST.get('min_amount', 1000),
                tagline=request.POST.get('tagline', ''),
            )
            messages.success(request, 'Finance provider added.')
        elif action == 'toggle':
            fp = get_object_or_404(FinanceProvider, pk=request.POST.get('fp_id'))
            fp.is_active = not fp.is_active
            fp.save()
        return redirect('uc1:finance_providers')
    return render(request, 'uc1_roofing/finance_providers.html', {'providers': providers})


def quote_finance(request, pk):
    """Show finance options for a specific quote."""
    _seed_finance_providers()
    quote     = get_object_or_404(Quote, pk=pk)
    providers = FinanceProvider.objects.filter(is_active=True)
    principal = float(quote.total_inc_gst)

    finance_options = []
    for fp in providers:
        options = []
        for term in [12, 24, 36, 48, 60]:
            if fp.min_term_months <= term <= fp.max_term_months and principal >= float(fp.min_amount):
                rate = float(fp.interest_rate_pct) / 100 / 12
                if rate == 0:
                    monthly = principal / term
                else:
                    monthly = (principal * rate) / (1 - (1 + rate) ** (-term))
                total_paid = monthly * term
                options.append({
                    'term': term,
                    'monthly': round(monthly, 2),
                    'total': round(total_paid, 2),
                    'interest': round(total_paid - principal, 2),
                })
        if options:
            finance_options.append({'provider': fp, 'options': options})

    return render(request, 'uc1_roofing/quote_finance.html', {
        'quote': quote, 'finance_options': finance_options, 'principal': principal,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 4 — Storm Lead Engine
# ═══════════════════════════════════════════════════════════════════════════════

def storm_dashboard(request):
    events     = StormEvent.objects.all()
    total_leads = StormLead.objects.count()
    won_leads   = StormLead.objects.filter(status='won').count()
    est_pipeline = sum(float(l.estimated_value) for l in
                       StormLead.objects.filter(status__in=['new','contacted','quoted']))

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create':
            event = StormEvent.objects.create(
                name=request.POST.get('name', ''),
                event_type=request.POST.get('event_type', 'hail'),
                event_date=request.POST.get('event_date'),
                severity=request.POST.get('severity', 3),
                affected_suburbs=request.POST.get('affected_suburbs', ''),
                state=request.POST.get('state', 'QLD'),
                notes=request.POST.get('notes', ''),
            )
            messages.success(request, f'Storm event "{event.name}" created.')
            return redirect('uc1:storm_detail', pk=event.pk)
        return redirect('uc1:storm_dashboard')

    return render(request, 'uc1_roofing/storm_dashboard.html', {
        'events': events, 'total_leads': total_leads,
        'won_leads': won_leads, 'est_pipeline': est_pipeline,
        'storm_type_choices': StormEvent._meta.get_field('event_type').choices,
        'severity_choices': StormEvent._meta.get_field('severity').choices,
    })


def storm_detail(request, pk):
    event  = get_object_or_404(StormEvent, pk=pk)
    leads  = event.leads.select_related('quote').all()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'scan':
            # Use Geoscape building footprint data to scan affected suburbs
            suburbs = [s.strip() for s in event.affected_suburbs.split(',') if s.strip()]
            added = 0
            for suburb_raw in suburbs:
                # Attempt Geoscape property search (simulated for POC without suburb→bbox geocoding)
                # In production: geocode suburb → bbox → Geoscape /buildings query
                # For now: flag as scanned, allow manual addition via form
                pass
            if added == 0:
                messages.info(request,
                    'Geoscape scan requires suburb geocoding in production. '
                    'Add leads manually below, or paste a CSV of addresses.')
            else:
                event.leads_generated = event.leads.count()
                event.save()
                messages.success(request, f'Scan complete — {added} new leads added.')

        elif action == 'import_csv':
            csv_text = request.POST.get('csv_text', '')
            added = 0
            for line in csv_text.strip().splitlines():
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 1 and parts[0]:
                    suburb = parts[1] if len(parts) > 1 else event.affected_suburbs.split(',')[0].strip()
                    StormLead.objects.create(
                        storm_event=event,
                        address=parts[0],
                        suburb=suburb,
                        state=event.state,
                        roof_area_sqm=float(parts[2]) if len(parts) > 2 else 0,
                        estimated_value=float(parts[3]) if len(parts) > 3 else 0,
                        contact_name=parts[4] if len(parts) > 4 else '',
                        contact_phone=parts[5] if len(parts) > 5 else '',
                        status='new',
                    )
                    added += 1
            event.leads_generated = event.leads.count()
            event.save()
            messages.success(request, f'Imported {added} leads.')

        elif action == 'add_lead':
            StormLead.objects.create(
                storm_event=event,
                address=request.POST.get('address', ''),
                suburb=request.POST.get('suburb', ''),
                state=event.state,
                roof_area_sqm=request.POST.get('roof_area_sqm') or 0,
                estimated_value=request.POST.get('estimated_value') or 0,
                contact_name=request.POST.get('contact_name', ''),
                contact_phone=request.POST.get('contact_phone', ''),
                contact_email=request.POST.get('contact_email', ''),
                status='new',
            )
            event.leads_generated = event.leads.count()
            event.save()
            messages.success(request, 'Lead added.')

        elif action == 'update_lead':
            lead = get_object_or_404(StormLead, pk=request.POST.get('lead_id'),
                                     storm_event=event)
            lead.status       = request.POST.get('status', lead.status)
            lead.contact_name = request.POST.get('contact_name', lead.contact_name)
            lead.contact_phone = request.POST.get('contact_phone', lead.contact_phone)
            lead.notes        = request.POST.get('notes', lead.notes)
            lead.save()

        return redirect('uc1:storm_detail', pk=pk)

    stats = {
        'total':     leads.count(),
        'new':       leads.filter(status='new').count(),
        'contacted': leads.filter(status='contacted').count(),
        'quoted':    leads.filter(status='quoted').count(),
        'won':       leads.filter(status='won').count(),
        'lost':      leads.filter(status='lost').count(),
        'pipeline':  sum(float(l.estimated_value) for l in
                         leads.filter(status__in=['new','contacted','quoted'])),
    }
    return render(request, 'uc1_roofing/storm_detail.html', {
        'event': event, 'leads': leads, 'stats': stats,
        'lead_status_choices': StormLead._meta.get_field('status').choices,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 5 — Roof Condition Report
# ═══════════════════════════════════════════════════════════════════════════════

def condition_report_list(request):
    reports = RoofConditionReport.objects.select_related('quote').all()
    total_value = sum(float(r.price_ex_gst) for r in reports)
    return render(request, 'uc1_roofing/condition_report_list.html', {
        'reports': reports, 'total_value': total_value,
    })


def condition_report_create(request, quote_pk):
    from core.claude_client import call_claude as _call_claude
    quote    = get_object_or_404(Quote, pk=quote_pk)
    existing = quote.condition_reports.first()
    lidar    = getattr(quote, 'lidar_analysis', None)

    if request.method == 'POST':
        report_type    = request.POST.get('report_type', 'homebuyer')
        client_name    = request.POST.get('client_name',
                         quote.contact.name if quote.contact else '')
        client_email   = request.POST.get('client_email',
                         quote.contact.email if quote.contact else '')
        client_company = request.POST.get('client_company', '')
        inspector_name = request.POST.get('inspector_name', '')
        price_ex_gst   = request.POST.get('price_ex_gst', 350)
        extra_notes    = request.POST.get('inspector_notes', '')

        # Build context for AI assessment
        lidar_block = ''
        if lidar:
            lidar_block = f"""
LiDAR Measurements:
  Perimeter: {lidar.perimeter_m:.0f} m
  Ridge height: {lidar.ridge_height_m or 'N/A'} m
  Eave height: {lidar.eave_height_m or 'N/A'} m
  Solar panels detected: {lidar.solar_panels}
  Scaffolding required: {lidar.scaffolding_required} ({lidar.scaffolding_risk_level} risk)
  LiDAR coverage: {lidar.get_lidar_coverage_display()}"""

        context_text = f"""Property: {quote.property_address}
Roof material: {quote.get_material_display()}
Roof pitch: {quote.get_pitch_type_display()}
Plan area: {quote.flat_area_sqm} m²  (adjusted: {quote.adjusted_area_sqm} m²)
{lidar_block}
Inspector notes: {extra_notes}
Report type: {report_type}"""

        system = """You are a licensed roof inspector in Australia generating a formal Roof Condition Report.
Assess the roof based on the provided data.
Return ONLY valid JSON with these exact keys:
  condition_grade: "A", "B", "C", "D", or "F"
  condition_score: integer 0-100 (100=perfect, 0=failed)
  life_remaining_years: integer
  urgency_level: one of "routine", "within_5_years", "within_2_years", "within_1_year", "immediate"
  assessment: 3 professional paragraphs (narrative condition description)
  recommended_works: numbered list of recommended works in priority order"""

        result   = _call_claude(system, context_text, max_tokens=1024)
        ai_data  = {}
        try:
            m = re.search(r'\{.*\}', result['content'], re.DOTALL)
            ai_data = json.loads(m.group()) if m else {}
        except Exception:
            pass

        report = RoofConditionReport.objects.create(
            quote=quote,
            report_type=report_type,
            client_name=client_name,
            client_email=client_email,
            client_company=client_company,
            condition_grade=ai_data.get('condition_grade', 'B'),
            condition_score=ai_data.get('condition_score', 70),
            life_remaining_years=ai_data.get('life_remaining_years', 10),
            urgency_level=ai_data.get('urgency_level', 'routine'),
            ai_assessment=ai_data.get('assessment', result['content'][:2000]),
            recommended_works=ai_data.get('recommended_works', ''),
            inspector_name=inspector_name,
            price_ex_gst=price_ex_gst,
            status='draft',
        )
        ExecutionLog.objects.create(
            tool_name='condition_report_generate',
            payload=json.dumps({'type': report_type, 'quote': quote.ref_number}),
            result=json.dumps({'report': report.report_number,
                               'grade': report.condition_grade,
                               'score': report.condition_score}),
            status='success', quote=quote,
        )
        messages.success(request, f'Report {report.report_number} generated (Grade {report.condition_grade}).')
        return redirect('uc1:condition_report_detail', pk=report.pk)

    return render(request, 'uc1_roofing/condition_report_create.html', {
        'quote': quote, 'lidar': lidar, 'existing': existing,
        'report_type_choices': RoofConditionReport._meta.get_field('report_type').choices,
    })


def condition_report_detail(request, pk):
    report = get_object_or_404(RoofConditionReport, pk=pk)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'finalise':
            report.status = 'final'
            report.save()
            messages.success(request, 'Report finalised.')
        elif action == 'deliver':
            report.status = 'delivered'
            report.save()
            messages.success(request, 'Report marked as delivered.')
        elif action == 'update_price':
            report.price_ex_gst = request.POST.get('price_ex_gst', report.price_ex_gst)
            report.save()
            messages.success(request, 'Price updated.')
        return redirect('uc1:condition_report_detail', pk=pk)

    return render(request, 'uc1_roofing/condition_report_detail.html', {'report': report})


def condition_report_print(request, pk):
    report = get_object_or_404(RoofConditionReport, pk=pk)
    return render(request, 'uc1_roofing/condition_report_print.html', {'report': report})
