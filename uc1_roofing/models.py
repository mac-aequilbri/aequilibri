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
