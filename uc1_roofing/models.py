"""UC1 Roofing Estimator — Database Models"""
from django.db import models
from django.utils import timezone
import json, math, uuid


PITCH_CHOICES = [
    ('flat',       'Flat 0°'),
    ('low',        'Low 10°'),
    ('standard',   'Standard 22°'),
    ('steep',      'Steep 35°'),
    ('very_steep', 'Very Steep 45°'),
]
PITCH_FACTORS = {
    'flat': 1.000, 'low': 1.015, 'standard': 1.082,
    'steep': 1.221, 'very_steep': 1.414,
}

QUOTE_STATUS = [
    ('draft',     'Draft'),
    ('sent',      'Sent'),
    ('accepted',  'Accepted'),
    ('declined',  'Declined'),
]

MATERIAL_CHOICES = [
    ('colorbond',   'Colorbond Steel'),
    ('terracotta',  'Terracotta Tiles'),
    ('concrete',    'Concrete Tiles'),
    ('zincalume',   'Zincalume'),
    ('slate',       'Natural Slate'),
    ('asphalt',     'Asphalt Shingles'),
]


class Contact(models.Model):
    name        = models.CharField(max_length=200)
    email       = models.EmailField(blank=True)
    phone       = models.CharField(max_length=30, blank=True)
    company     = models.CharField(max_length=200, blank=True)
    address     = models.TextField(blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name}" + (f" ({self.company})" if self.company else "")

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Contact'


class RateCard(models.Model):
    material        = models.CharField(max_length=50, choices=MATERIAL_CHOICES)
    pitch_type      = models.CharField(max_length=20, choices=PITCH_CHOICES)
    description     = models.CharField(max_length=300)
    unit            = models.CharField(max_length=20, default='m²')
    rate_ex_gst     = models.DecimalField(max_digits=10, decimal_places=2,
                                          help_text='Rate per unit, excluding GST (AUD)')
    is_active       = models.BooleanField(default=True)
    updated_at      = models.DateTimeField(auto_now=True)

    @property
    def gst_amount(self):
        return round(float(self.rate_ex_gst) * 0.10, 2)

    @property
    def rate_inc_gst(self):
        return round(float(self.rate_ex_gst) * 1.10, 2)

    def __str__(self):
        return f"{self.get_material_display()} / {self.get_pitch_type_display()} — ${self.rate_ex_gst}/{self.unit}"

    class Meta:
        ordering = ['material', 'pitch_type']
        verbose_name = 'Rate Card'
        unique_together = [('material', 'pitch_type')]


class Quote(models.Model):
    ref_number      = models.CharField(max_length=20, unique=True, editable=False)
    contact         = models.ForeignKey(Contact, on_delete=models.SET_NULL,
                                        null=True, blank=True, related_name='quotes')
    property_address = models.TextField()
    flat_area_sqm   = models.DecimalField(max_digits=10, decimal_places=2,
                                           help_text='Flat (plan) roof area in m²')
    pitch_type      = models.CharField(max_length=20, choices=PITCH_CHOICES, default='standard')
    waste_factor_pct = models.DecimalField(max_digits=5, decimal_places=1, default=10.0,
                                            help_text='Waste allowance %')
    material        = models.CharField(max_length=50, choices=MATERIAL_CHOICES, default='colorbond')
    notes               = models.TextField(blank=True)
    status              = models.CharField(max_length=20, choices=QUOTE_STATUS, default='draft')
    # Roof geometry captured at quote-creation time for the PDF roof plan
    roof_polygon_json   = models.TextField(blank=True, default='',
                              help_text='JSON [[lat,lon],...] building footprint from MS footprints')
    roof_sections_json  = models.TextField(blank=True, default='',
                              help_text='JSON Solar API section list (facing, area, bbox, pitch)')
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    @property
    def pitch_factor(self):
        return PITCH_FACTORS.get(self.pitch_type, 1.0)

    @property
    def adjusted_area_sqm(self):
        """Area after pitch and waste adjustments."""
        return round(float(self.flat_area_sqm) * self.pitch_factor * (1 + float(self.waste_factor_pct) / 100), 2)

    @property
    def subtotal_ex_gst(self):
        return round(sum(float(i.line_total_ex_gst) for i in self.items.all()), 2)

    @property
    def gst_total(self):
        return round(self.subtotal_ex_gst * 0.10, 2)

    @property
    def total_inc_gst(self):
        return round(self.subtotal_ex_gst + self.gst_total, 2)

    # ── Notes are split into customer-visible job notes and an internal
    # pricing breakdown by the marker "═══ Internal".  customer_notes is
    # what appears on the printed PDF; internal_notes_text is shown only on
    # the admin/detail page.
    _INTERNAL_NOTES_MARKER = '═══ Internal pricing breakdown'

    @property
    def customer_notes(self):
        """Public job-notes shown on the printed quote (no pricing breakdown)."""
        text = self.notes or ''
        if self._INTERNAL_NOTES_MARKER in text:
            text = text.split(self._INTERNAL_NOTES_MARKER, 1)[0]
        return text.strip()

    @property
    def internal_notes_text(self):
        """Internal pricing breakdown (Port City line-by-line). Detail page only."""
        text = self.notes or ''
        if self._INTERNAL_NOTES_MARKER in text:
            return text.split(self._INTERNAL_NOTES_MARKER, 1)[1].strip()
        return ''

    # ── Address parsing helpers used by the print template ─────────────────
    @property
    def address_suburb(self):
        """Suburb extracted from ``property_address``.

        Australian formats handled:
          "11 Ahern St, Ayr QLD 4807"     → "Ayr"
          "11 Ahern St, Ayr QLD 4807, AU" → "Ayr"
          "5 Richardson St Douglas QLD"   → "Douglas"
        """
        import re as _re
        text = str(self.property_address or '').strip()
        if not text:
            return ''
        # Strip trailing country
        text = _re.sub(r',\s*(Australia|AU)\s*$', '', text, flags=_re.IGNORECASE)
        # Split by comma
        parts = [p.strip() for p in text.split(',') if p.strip()]
        if len(parts) >= 2:
            suburb_part = parts[1]
            # Strip state + postcode from end
            suburb_part = _re.sub(
                r'\s+(QLD|NSW|VIC|SA|WA|TAS|ACT|NT)(\s+\d{4})?\s*$',
                '', suburb_part, flags=_re.IGNORECASE,
            ).strip()
            return suburb_part
        # No comma — try to extract suburb from "addr suburb STATE pcode"
        m = _re.search(
            r'\b([A-Z][A-Za-z\s]+?)\s+(QLD|NSW|VIC|SA|WA|TAS|ACT|NT)\b',
            text,
        )
        return (m.group(1).strip() if m else '')

    @property
    def address_postcode(self):
        """4-digit postcode from ``property_address`` if present."""
        import re as _re
        m = _re.search(r'\b(\d{4})\b', str(self.property_address or ''))
        return m.group(1) if m else ''

    @property
    def display_name(self):
        """Customer name for the printed quote.  When the form was submitted
        with an address-as-name (common when the user clicks an address and
        the JS auto-populates), strip that and return a placeholder so the
        printed quote does NOT show the address twice."""
        name = (self.contact.name if self.contact_id and self.contact else '') or ''
        name = name.strip()
        if not name:
            return ''
        # Detect "address-as-name" — name starts with a number then street word
        import re as _re
        if _re.match(r'^\d+[\s/]', name):
            return ''  # looks like an address, not a name
        # Detect name == property_address
        if self.property_address and name in self.property_address:
            return ''
        return name

    def save(self, *args, **kwargs):
        if not self.ref_number:
            from datetime import date
            d = date.today().strftime('%Y%m%d')
            last = Quote.objects.filter(ref_number__startswith=f'REF-{d}').count()
            self.ref_number = f'REF-{d}-{last+1:04d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.ref_number} — {self.property_address[:60]}"

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Quote'


class QuoteItem(models.Model):
    quote           = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name='items')
    description     = models.CharField(max_length=300)
    quantity        = models.DecimalField(max_digits=10, decimal_places=2)
    unit            = models.CharField(max_length=20, default='m²')
    unit_price_ex_gst = models.DecimalField(max_digits=10, decimal_places=2)
    sort_order      = models.PositiveSmallIntegerField(default=0)

    @property
    def line_total_ex_gst(self):
        return round(float(self.quantity) * float(self.unit_price_ex_gst), 2)

    @property
    def gst_amount(self):
        return round(self.line_total_ex_gst * 0.10, 2)

    @property
    def line_total_inc_gst(self):
        return round(self.line_total_ex_gst * 1.10, 2)

    def __str__(self):
        return f"{self.description} × {self.quantity}"

    class Meta:
        ordering = ['sort_order', 'id']
        verbose_name = 'Quote Item'


class RoofPolygon(models.Model):
    """Stores detected or manually drawn roof polygon data."""
    quote           = models.OneToOneField(Quote, on_delete=models.CASCADE,
                                            related_name='polygon', null=True, blank=True)
    coordinates_json = models.TextField(default='[]',
                                         help_text='JSON array of [lat, lng] pairs')
    detection_path  = models.CharField(max_length=50, blank=True,
                                        help_text='e.g. Path 1 — OSM, Manual')
    confidence      = models.CharField(max_length=20, blank=True,
                                        help_text='High / Medium / Low')
    area_sqm_raw    = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at      = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Polygon for Quote #{self.quote_id} via {self.detection_path}"

    class Meta:
        verbose_name = 'Roof Polygon'


class BuildingFootprint(models.Model):
    """Microsoft ML-derived building footprint polygon for a single structure."""
    min_lat      = models.FloatField()
    max_lat      = models.FloatField()
    min_lon      = models.FloatField()
    max_lon      = models.FloatField()
    centroid_lat = models.FloatField()
    centroid_lon = models.FloatField()
    area_sqm     = models.FloatField()
    # Outer-ring coordinates stored as JSON: [[lon, lat], ...]
    geometry     = models.TextField()

    class Meta:
        indexes = [
            models.Index(fields=['min_lat', 'max_lat'], name='bf_lat_idx'),
            models.Index(fields=['min_lon', 'max_lon'], name='bf_lon_idx'),
        ]
        verbose_name = 'Building Footprint (ML)'

    def __str__(self):
        return f"BuildingFootprint {self.id} — {self.area_sqm:.0f} m² @ ({self.centroid_lat:.4f}, {self.centroid_lon:.4f})"


PRICE_CHECK_STATUS = [
    ('success',  'Success'),
    ('partial',  'Partial — some prices updated'),
    ('no_change','No Change'),
    ('error',    'Error'),
]


class PriceCheckLog(models.Model):
    """Records every run of the check_vendor_prices process."""
    run_at          = models.DateTimeField(auto_now_add=True)
    status          = models.CharField(max_length=20, choices=PRICE_CHECK_STATUS, default='success')
    vendors_checked = models.PositiveSmallIntegerField(default=0)
    prices_updated  = models.PositiveSmallIntegerField(default=0)
    prices_unchanged = models.PositiveSmallIntegerField(default=0)
    errors          = models.PositiveSmallIntegerField(default=0)
    summary         = models.TextField(blank=True, help_text='Human-readable summary of changes')
    raw_log         = models.TextField(blank=True, help_text='Detailed line-by-line log')

    def __str__(self):
        return f"[{self.run_at:%Y-%m-%d %H:%M}] {self.get_status_display()} — {self.prices_updated} updated"

    class Meta:
        ordering = ['-run_at']
        verbose_name = 'Price Check Log'


PO_STATUS = [
    ('draft',     'Draft'),
    ('sent',      'Sent to Vendor'),
    ('confirmed', 'Confirmed'),
    ('cancelled', 'Cancelled'),
]


class Vendor(models.Model):
    """Preferred material supplier."""
    name           = models.CharField(max_length=200)
    contact_name   = models.CharField(max_length=200, blank=True)
    contact_email  = models.EmailField(blank=True)
    contact_phone  = models.CharField(max_length=30, blank=True)
    website        = models.URLField(blank=True)
    suburb         = models.CharField(max_length=100, blank=True)
    state          = models.CharField(max_length=10, blank=True, default='QLD')
    notes          = models.TextField(blank=True)
    is_preferred   = models.BooleanField(default=False,
                                          help_text='Highlight as preferred vendor')
    is_active      = models.BooleanField(default=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-is_preferred', 'name']
        verbose_name = 'Vendor'


class VendorMaterialPrice(models.Model):
    """Vendor's price for a roofing material line item."""
    vendor         = models.ForeignKey(Vendor, on_delete=models.CASCADE,
                                        related_name='prices')
    material       = models.CharField(max_length=50, choices=MATERIAL_CHOICES)
    item_code      = models.CharField(max_length=50, blank=True,
                                       help_text='Vendor SKU / product code')
    description    = models.CharField(max_length=300)
    unit           = models.CharField(max_length=20, default='m²')
    unit_price_ex_gst = models.DecimalField(max_digits=10, decimal_places=2)
    lead_days         = models.PositiveSmallIntegerField(default=3,
                                                          help_text='Typical lead time in business days')
    price_source_url  = models.URLField(blank=True,
                                         help_text='Vendor product page used for price verification')
    previous_price    = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                             help_text='Price before last update (for change tracking)')
    last_verified     = models.DateTimeField(null=True, blank=True,
                                              help_text='When this price was last confirmed against vendor source')
    is_available      = models.BooleanField(default=True)
    updated_at        = models.DateTimeField(auto_now=True)

    @property
    def unit_price_inc_gst(self):
        return round(float(self.unit_price_ex_gst) * 1.10, 2)

    def __str__(self):
        return f"{self.vendor.name} — {self.get_material_display()} @ ${self.unit_price_ex_gst}/{self.unit}"

    class Meta:
        ordering = ['material', 'unit_price_ex_gst']
        verbose_name = 'Vendor Material Price'
        unique_together = [('vendor', 'material')]


class PurchaseOrder(models.Model):
    """Purchase order raised from a quote, directed to a vendor."""
    po_number      = models.CharField(max_length=20, unique=True, editable=False)
    quote          = models.ForeignKey(Quote, on_delete=models.SET_NULL,
                                        null=True, blank=True, related_name='purchase_orders')
    vendor         = models.ForeignKey(Vendor, on_delete=models.PROTECT,
                                        related_name='purchase_orders')
    status         = models.CharField(max_length=20, choices=PO_STATUS, default='draft')
    delivery_address = models.TextField(blank=True,
                                         help_text='Defaults to property address from quote')
    requested_delivery_date = models.DateField(null=True, blank=True)
    notes          = models.TextField(blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    @property
    def subtotal_ex_gst(self):
        return round(sum(float(i.line_total_ex_gst) for i in self.po_items.all()), 2)

    @property
    def gst_total(self):
        return round(self.subtotal_ex_gst * 0.10, 2)

    @property
    def total_inc_gst(self):
        return round(self.subtotal_ex_gst + self.gst_total, 2)

    def save(self, *args, **kwargs):
        if not self.po_number:
            from datetime import date
            d = date.today().strftime('%Y%m%d')
            last = PurchaseOrder.objects.filter(po_number__startswith=f'PO-{d}').count()
            self.po_number = f'PO-{d}-{last+1:04d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.po_number} → {self.vendor.name}"

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Purchase Order'


class PurchaseOrderItem(models.Model):
    """A single line on a purchase order."""
    purchase_order    = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE,
                                           related_name='po_items')
    description       = models.CharField(max_length=300)
    item_code         = models.CharField(max_length=50, blank=True)
    quantity          = models.DecimalField(max_digits=10, decimal_places=2)
    unit              = models.CharField(max_length=20, default='m²')
    unit_price_ex_gst = models.DecimalField(max_digits=10, decimal_places=2)
    sort_order        = models.PositiveSmallIntegerField(default=0)

    @property
    def line_total_ex_gst(self):
        return round(float(self.quantity) * float(self.unit_price_ex_gst), 2)

    @property
    def line_total_inc_gst(self):
        return round(self.line_total_ex_gst * 1.10, 2)

    def __str__(self):
        return f"{self.description} × {self.quantity}"

    class Meta:
        ordering = ['sort_order', 'id']
        verbose_name = 'Purchase Order Item'


LIDAR_COVERAGE = [
    ('full',      'Full — 1m LiDAR DSM + DTM'),
    ('partial',   'Partial — DSM only'),
    ('estimated', 'Estimated — storey-based fallback'),
    ('none',      'None — no data available'),
]

RISK_LEVEL = [
    ('low',    'Low'),
    ('medium', 'Medium'),
    ('high',   'High'),
]


class RoofLidarAnalysis(models.Model):
    """
    Stores the output of the LiDAR / roof analysis service for a quote.
    One record per quote — re-analysing overwrites the previous result.
    """
    quote                = models.OneToOneField(Quote, on_delete=models.CASCADE,
                                                related_name='lidar_analysis')
    # ── Perimeter & guttering ────────────────────────────────────────────────
    perimeter_m          = models.FloatField(default=0,
                                              help_text='Building perimeter in metres')
    guttering_linear_m   = models.FloatField(default=0,
                                              help_text='Estimated guttering linear metres')
    # ── Heights from LiDAR ───────────────────────────────────────────────────
    ridge_height_m       = models.FloatField(null=True, blank=True,
                                              help_text='Roof ridge height above ground (m)')
    eave_height_m        = models.FloatField(null=True, blank=True,
                                              help_text='Eave height above ground (m)')
    height_range_m       = models.FloatField(null=True, blank=True,
                                              help_text='Vertical height of roof (ridge − eave)')
    # ── Scaffolding ──────────────────────────────────────────────────────────
    scaffolding_required     = models.BooleanField(default=False)
    scaffolding_linear_m     = models.FloatField(default=0,
                                                  help_text='Estimated scaffolding linear metres')
    scaffolding_risk_level   = models.CharField(max_length=10, choices=RISK_LEVEL,
                                                 default='low')
    scaffolding_reason       = models.CharField(max_length=200, blank=True)
    # ── Multi-structure ──────────────────────────────────────────────────────
    structure_count      = models.PositiveSmallIntegerField(default=1,
                                                             help_text='Number of structures detected on lot')
    structures_json      = models.TextField(default='[]',
                                             help_text='JSON list of all detected structures')
    # ── Rooftop features ─────────────────────────────────────────────────────
    solar_panels         = models.BooleanField(default=False)
    solar_hw             = models.BooleanField(default=False,
                                               help_text='Solar hot water system present')
    # ── Data quality ─────────────────────────────────────────────────────────
    lidar_coverage       = models.CharField(max_length=20, choices=LIDAR_COVERAGE,
                                             default='none')
    data_source          = models.CharField(max_length=50, blank=True)
    analysis_notes       = models.TextField(blank=True,
                                             help_text='JSON list of analysis notes')
    elapsed_ms           = models.IntegerField(default=0)
    analyzed_at          = models.DateTimeField(auto_now=True)

    def __str__(self):
        return (f"LiDAR Analysis for {self.quote.ref_number} — "
                f"eave {self.eave_height_m or '?'} m, "
                f"perimeter {self.perimeter_m:.0f} m")

    class Meta:
        verbose_name = 'Roof LiDAR Analysis'


class ExecutionLog(models.Model):
    """Append-only audit log for all AI tool executions — UC1."""
    tool_name   = models.CharField(max_length=100)
    payload     = models.TextField(default='{}')
    result      = models.TextField(default='{}')
    status      = models.CharField(max_length=20, default='success')
    duration_ms = models.IntegerField(default=0)
    quote       = models.ForeignKey(Quote, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='execution_logs')
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.tool_name} — {self.status}"

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Execution Log (UC1)'


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 1 — Guttering Auto-Quote
# ═══════════════════════════════════════════════════════════════════════════════

GUTTERING_ITEM_TYPES = [
    ('gutter',    'Guttering (per linear metre)'),
    ('downpipe',  'Downpipe (each)'),
    ('fascia',    'Fascia Board (per linear metre)'),
    ('valley',    'Valley Iron (per linear metre)'),
    ('ridge_cap', 'Ridge Cap (per linear metre)'),
]


class GutteringRate(models.Model):
    item_type   = models.CharField(max_length=30, choices=GUTTERING_ITEM_TYPES)
    description = models.CharField(max_length=200)
    unit        = models.CharField(max_length=20, default='lm')
    rate_ex_gst = models.DecimalField(max_digits=10, decimal_places=2)
    is_active   = models.BooleanField(default=True)
    updated_at  = models.DateTimeField(auto_now=True)

    def __str__(self): return f"{self.get_item_type_display()} — ${self.rate_ex_gst}/{self.unit}"
    class Meta: verbose_name = 'Guttering Rate'; ordering = ['item_type']


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 2 — Solar Bundle
# ═══════════════════════════════════════════════════════════════════════════════

class SolarPartner(models.Model):
    name               = models.CharField(max_length=200)
    contact_name       = models.CharField(max_length=200, blank=True)
    contact_email      = models.EmailField(blank=True)
    contact_phone      = models.CharField(max_length=30, blank=True)
    referral_fee_pct   = models.DecimalField(max_digits=5, decimal_places=2, default=10.0,
                                              help_text='% of solar install value paid as referral fee')
    avg_install_value  = models.DecimalField(max_digits=10, decimal_places=2, default=10000,
                                              help_text='Average system value for commission estimate')
    is_active          = models.BooleanField(default=True)
    notes              = models.TextField(blank=True)
    created_at         = models.DateTimeField(auto_now_add=True)

    def __str__(self): return self.name
    class Meta: verbose_name = 'Solar Partner'; ordering = ['name']


SOLAR_REFERRAL_STATUS = [
    ('pending',   'Pending'),
    ('submitted', 'Submitted to Partner'),
    ('contacted', 'Client Contacted'),
    ('won',       'Won'),
    ('lost',      'Lost'),
]


class SolarReferral(models.Model):
    quote                  = models.ForeignKey(Quote, on_delete=models.CASCADE,
                                                related_name='solar_referrals')
    partner                = models.ForeignKey(SolarPartner, on_delete=models.SET_NULL,
                                                null=True, blank=True)
    status                 = models.CharField(max_length=20, choices=SOLAR_REFERRAL_STATUS,
                                               default='pending')
    solar_potential_kwh    = models.FloatField(default=0)
    best_section_area      = models.FloatField(default=0)
    best_section_facing    = models.CharField(max_length=20, blank=True)
    estimated_capacity_kw  = models.FloatField(default=0)
    estimated_install_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    estimated_referral_fee  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    client_notes           = models.TextField(blank=True)
    submitted_at           = models.DateTimeField(null=True, blank=True)
    created_at             = models.DateTimeField(auto_now_add=True)

    def __str__(self): return f"Solar Referral: {self.quote.ref_number}"
    class Meta: verbose_name = 'Solar Referral'; ordering = ['-created_at']


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 3 — Finance Integration
# ═══════════════════════════════════════════════════════════════════════════════

class FinanceProvider(models.Model):
    name              = models.CharField(max_length=100)
    slug              = models.CharField(max_length=30, unique=True)
    interest_rate_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                             help_text='Annual %. 0 = interest-free.')
    min_term_months   = models.IntegerField(default=12)
    max_term_months   = models.IntegerField(default=60)
    min_amount        = models.DecimalField(max_digits=10, decimal_places=2, default=1000)
    tagline           = models.CharField(max_length=200, blank=True)
    is_active         = models.BooleanField(default=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    def __str__(self): return self.name
    class Meta: verbose_name = 'Finance Provider'; ordering = ['name']


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 4 — Storm Lead Engine
# ═══════════════════════════════════════════════════════════════════════════════

STORM_TYPES = [
    ('hail',    'Hailstorm'),
    ('cyclone', 'Cyclone'),
    ('wind',    'Severe Wind'),
    ('flood',   'Flooding / Water Ingress'),
    ('fire',    'Ember Attack / Bushfire'),
]
STORM_SEVERITY = [(i, f'{i} — {"Low" if i<=2 else "Moderate" if i==3 else "Severe" if i==4 else "Extreme"}')
                  for i in range(1, 6)]

LEAD_STATUS = [
    ('new',       'New — Not Contacted'),
    ('contacted', 'Contacted'),
    ('quoted',    'Quoted'),
    ('won',       'Won'),
    ('lost',      'Lost / No Response'),
]


class StormEvent(models.Model):
    name             = models.CharField(max_length=200)
    event_type       = models.CharField(max_length=20, choices=STORM_TYPES, default='hail')
    event_date       = models.DateField()
    severity         = models.IntegerField(choices=STORM_SEVERITY, default=3)
    affected_suburbs = models.TextField(help_text='Comma-separated list of affected suburbs')
    state            = models.CharField(max_length=10, default='QLD')
    notes            = models.TextField(blank=True)
    leads_generated  = models.IntegerField(default=0)
    created_at       = models.DateTimeField(auto_now_add=True)

    def __str__(self): return f"{self.name} ({self.event_date})"
    class Meta: verbose_name = 'Storm Event'; ordering = ['-event_date']


class StormLead(models.Model):
    storm_event      = models.ForeignKey(StormEvent, on_delete=models.CASCADE,
                                          related_name='leads')
    address          = models.CharField(max_length=300)
    suburb           = models.CharField(max_length=100)
    state            = models.CharField(max_length=10, default='QLD')
    roof_area_sqm    = models.FloatField(default=0)
    estimated_value  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status           = models.CharField(max_length=20, choices=LEAD_STATUS, default='new')
    contact_name     = models.CharField(max_length=200, blank=True)
    contact_phone    = models.CharField(max_length=30, blank=True)
    contact_email    = models.EmailField(blank=True)
    notes            = models.TextField(blank=True)
    quote            = models.ForeignKey(Quote, on_delete=models.SET_NULL,
                                          null=True, blank=True, related_name='storm_leads')
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    def __str__(self): return f"{self.address} ({self.storm_event.name})"
    class Meta: verbose_name = 'Storm Lead'; ordering = ['-created_at']


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 5 — Roof Condition Report
# ═══════════════════════════════════════════════════════════════════════════════

CONDITION_GRADE = [
    ('A', 'A — Excellent (new/near-new, no action)'),
    ('B', 'B — Good (minor maintenance, 10+ years life)'),
    ('C', 'C — Fair (scheduled replacement within 5 years)'),
    ('D', 'D — Poor (replacement within 2 years recommended)'),
    ('F', 'F — Failed (immediate replacement required)'),
]
REPORT_TYPE = [
    ('homebuyer',    'Pre-Purchase Inspection'),
    ('insurance',    'Insurance Assessment'),
    ('maintenance',  'Routine Maintenance Report'),
    ('strata',       'Strata / Body Corporate'),
]
REPORT_STATUS = [('draft', 'Draft'), ('final', 'Final'), ('delivered', 'Delivered')]
URGENCY = [
    ('routine',       'Routine — No urgency'),
    ('within_5_years','Within 5 Years'),
    ('within_2_years','Within 2 Years'),
    ('within_1_year', 'Within 1 Year'),
    ('immediate',     'Immediate Action Required'),
]


class RoofConditionReport(models.Model):
    quote                = models.ForeignKey(Quote, on_delete=models.CASCADE,
                                              related_name='condition_reports')
    report_number        = models.CharField(max_length=20, unique=True, editable=False)
    report_type          = models.CharField(max_length=20, choices=REPORT_TYPE, default='homebuyer')
    client_name          = models.CharField(max_length=200, blank=True)
    client_email         = models.EmailField(blank=True)
    client_company       = models.CharField(max_length=200, blank=True)
    condition_grade      = models.CharField(max_length=2, choices=CONDITION_GRADE, default='B')
    condition_score      = models.IntegerField(default=70, help_text='0-100 overall score')
    life_remaining_years = models.IntegerField(default=10)
    urgency_level        = models.CharField(max_length=20, choices=URGENCY, default='routine')
    ai_assessment        = models.TextField(blank=True)
    recommended_works    = models.TextField(blank=True)
    inspector_name       = models.CharField(max_length=200, blank=True)
    price_ex_gst         = models.DecimalField(max_digits=8, decimal_places=2, default=350)
    status               = models.CharField(max_length=20, choices=REPORT_STATUS, default='draft')
    generated_at         = models.DateTimeField(auto_now_add=True)
    updated_at           = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.report_number:
            from datetime import date as _date
            d = _date.today().strftime('%Y%m%d')
            count = RoofConditionReport.objects.filter(
                report_number__startswith=f'RCR-{d}').count()
            self.report_number = f'RCR-{d}-{count+1:04d}'
        super().save(*args, **kwargs)

    @property
    def price_inc_gst(self):
        return round(float(self.price_ex_gst) * 1.10, 2)

    @property
    def grade_color(self):
        return {'A': '#27ae60', 'B': '#2ecc71', 'C': '#f39c12',
                'D': '#e67e22', 'F': '#e74c3c'}.get(self.condition_grade, '#888')

    def __str__(self): return f"{self.report_number} — {self.quote.property_address[:60]}"
    class Meta: verbose_name = 'Roof Condition Report'; ordering = ['-generated_at']
