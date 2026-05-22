"""Port City Roofing pricing — replicated from their Estimating Calculation
worksheets and validated against 8 historical quotes (Condron Place AYR,
Toomulla Esplanade, Estate St West End, Abbott St Oonoonba, Calliandra Ct
Mt Louisa, Lynham Douglas, Bullard Mysterton, Hilleard Rangewood, ...).

The pricing matrix below is keyed to the 08.01.2026 sheet revision.

Formula (per quote):
    internal = Σ (item_qty × item_rate)
    quoted   = internal × (1 + markup_factor)         # markup 0.05 or 0.10
    gst      = quoted × 0.10
    total    = quoted + gst

Gutters are calculated as a SEPARATE roll-up (gutters_lm × rate + downpipes +
travel) and the gutter total is added to the quoted price as an additional
scope item.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any


# ──────────────────────────────────────────────────────────────────────────────
# Constant rate table (snapshot as of 08.01.2026, with the 04.02.2026 variants
# for "Ultra Roof" splitting into Gable/Hip seen on the Toomulla worksheet).
# ──────────────────────────────────────────────────────────────────────────────

ROOF_RATE_PER_M2 = {
    'gable':      120.0,   # Simple roof — single or 2-section gable
    'hip':        130.0,   # Standard QLD residential hip
    'ultra':      140.0,   # Complex multi-wing / multi-pitch
    'ultra_gable': 140.0,  # Variant seen on Toomulla — high-spec gable
    'ultra_hip':   155.0,  # Variant seen on Toomulla — high-spec hip
}

EDGE_PROTECTION_PER_LM = 19.0
FUSE_PULL_FLAT         = 500.0
CRANE_PER_HR           = 500.0
BINS_PER_200M2         = 1600.0      # 1 bin per 200 m² of roof (round up)
FASCIA_COVER_PER_LM    = 65.0
DELIVERY_TRANSPORT     = 1000.0
COMMERCIAL_CERT        = 0           # variable

# Curving / Bullnose
CURVE_QUALITY_PER_M2   = 35.0        # .6 curve (bullnose)
CURVING_SHEET_RATE     = 30.0        # per sheet

# Tile / Batten / Insulation
TILE_REPLACE_PER_M2    = 140.0
BATTEN_REPLACE_PER_LM  = 16.5
CEILING_BATTS_PER_M2   = 35.0
RE_SCREW_PER_UNIT      = 25.0        # ridge-end screw
WIRE_PER_M             = 10.0

# Asbestos
ASBESTOS_STARTING_AT   = 252.0
ASBESTOS_HIGHSET_ALLOW = 1485.0
DECROMASTIC_PER_M2     = 110.0

# Solar
SOLAR_HIGHSET_ALLOW    = 250.0
SOLAR_TRAVEL_ALLOW     = 1254.20     # "Pinnacles worst case" — flat fee when remote
SOLAR_PANEL_RR         = 126.0       # Remove & Replace
SOLAR_PANEL_REMOVE     = 123.0       # Remove only
SOLAR_HW_RR            = 1800.0      # Hot Water R&R
SOLAR_HW_REMOVE        = 1000.0      # Hot Water Remove only
SOLAR_TUBE_RR          = 2000.0      # Evacuated-tube style R&R
SKYLIGHT_RR            = 800.0

# Gutter / Downpipe (separate sub-quote)
GUTTER_PER_LM          = 100.0
DOWNPIPE_90MM          = 250.0
DOWNPIPE_100MM         = 350.0
BOX_GUTTER_PER_LM      = 0           # variable

# Travel
TRAVEL_RATES = {
    'ayr_ingham':       600.0,    # Ayr, Home Hill, Ingham, Burdekin region
    'charters':         700.0,    # Charters Towers
    'cairns_mackay':    2000.0,   # Cairns / Mackay
    'magnetic_island':  0,        # variable
}

DEFAULT_MARKUP = 0.10              # quoted = internal × 1.10
GST_RATE       = 0.10


# ──────────────────────────────────────────────────────────────────────────────
# Travel zone detection by suburb / postcode
# ──────────────────────────────────────────────────────────────────────────────

# Suburb / postcode → (travel_zone_key, default_days)
# Days reflect typical job duration; Condron Place was 3 days (Ayr region).
TRAVEL_ZONES: dict[str, tuple[str, int]] = {
    # Townsville metro — no travel allowance
    'townsville':         ('local', 0),
    'garbutt':            ('local', 0),
    'kelso':              ('local', 0),
    'kirwan':             ('local', 0),
    'annandale':          ('local', 0),
    'mt_louisa':          ('local', 0),
    'mountlouisa':        ('local', 0),
    'oonoonba':           ('local', 0),
    'douglas':            ('local', 0),
    'west_end':           ('local', 0),
    'westend':            ('local', 0),
    'belgian_gardens':    ('local', 0),
    'belgiangardens':     ('local', 0),
    'mysterton':          ('local', 0),
    'rangewood':          ('local', 0),
    'magnetic_island':    ('magnetic_island', 0),
    'magneticisland':     ('magnetic_island', 0),

    # Ayr / Burdekin / Ingham route
    'ayr':                ('ayr_ingham', 3),
    'home_hill':          ('ayr_ingham', 3),
    'homehill':           ('ayr_ingham', 3),
    'ingham':             ('ayr_ingham', 3),
    'rita_island':        ('ayr_ingham', 3),
    'ritaisland':         ('ayr_ingham', 3),
    'brandon':            ('ayr_ingham', 3),
    'giru':               ('ayr_ingham', 3),

    # Toomulla / Paluma — Charters route (~ 1.5 hr)
    'toomulla':           ('ayr_ingham', 1),   # Toomulla is between Townsville/Ingham
    'paluma':             ('charters', 1),
    'rollingstone':       ('ayr_ingham', 1),
    'crystal_creek':      ('ayr_ingham', 1),
    'crystalcreek':       ('ayr_ingham', 1),

    # Charters Towers
    'charters_towers':    ('charters', 2),
    'charterstowers':     ('charters', 2),

    # Cairns / Mackay
    'cairns':             ('cairns_mackay', 5),
    'mackay':             ('cairns_mackay', 5),
}

POSTCODE_ZONES: dict[str, tuple[str, int]] = {
    # Ayr / Burdekin
    '4807': ('ayr_ingham', 3),
    '4806': ('ayr_ingham', 3),
    '4808': ('ayr_ingham', 3),
    # Ingham
    '4850': ('ayr_ingham', 3),
    # Toomulla / Paluma / Rollingstone
    '4816': ('charters', 1),
    # Charters Towers
    '4820': ('charters', 2),
    # Townsville metro (most local addresses)
    '4810': ('local', 0),
    '4811': ('local', 0),
    '4812': ('local', 0),
    '4813': ('local', 0),
    '4814': ('local', 0),
    '4815': ('local', 0),
    '4817': ('local', 0),
    '4818': ('local', 0),
}


def _normalize(text: str) -> str:
    """Lowercase + strip non-alphanumeric for fuzzy matching."""
    return re.sub(r'[^a-z0-9]+', '', str(text or '').lower())


def detect_travel_zone(address: str = '', suburb: str = '',
                       postcode: str = '') -> tuple[str, int, float]:
    """Return ``(zone_key, days, daily_rate)`` for the given address.

    Heuristics, in priority order:
      1. Explicit suburb keyword match (most reliable)
      2. Postcode lookup
      3. Substring scan of the address string
      4. Default to ``('local', 0, 0)``
    """
    key_suburb = _normalize(suburb)
    if key_suburb and key_suburb in TRAVEL_ZONES:
        zone, days = TRAVEL_ZONES[key_suburb]
        return zone, days, TRAVEL_RATES.get(zone, 0.0)

    pc = re.search(r'\b(\d{4})\b', str(postcode or ''))
    if pc and pc.group(1) in POSTCODE_ZONES:
        zone, days = POSTCODE_ZONES[pc.group(1)]
        return zone, days, TRAVEL_RATES.get(zone, 0.0)

    address_norm = _normalize(address)
    for sub, (zone, days) in TRAVEL_ZONES.items():
        if sub and sub in address_norm:
            return zone, days, TRAVEL_RATES.get(zone, 0.0)

    pc2 = re.search(r'\b(\d{4})\b', str(address or ''))
    if pc2 and pc2.group(1) in POSTCODE_ZONES:
        zone, days = POSTCODE_ZONES[pc2.group(1)]
        return zone, days, TRAVEL_RATES.get(zone, 0.0)

    return 'local', 0, 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Quote builder — produces a list of line items + totals
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class LineItem:
    description: str
    quantity:    float
    unit:        str
    rate:        float

    @property
    def amount(self) -> float:
        return round(self.quantity * self.rate, 2)


@dataclass
class PortCityQuote:
    items:       list[LineItem] = field(default_factory=list)
    gutter_items:list[LineItem] = field(default_factory=list)
    markup_pct:  float = DEFAULT_MARKUP

    def add(self, desc: str, qty: float, unit: str, rate: float,
            *, gutter: bool = False) -> None:
        if qty <= 0 or rate <= 0:
            return
        item = LineItem(desc, round(qty, 2), unit, rate)
        (self.gutter_items if gutter else self.items).append(item)

    @property
    def internal_subtotal(self) -> float:
        return round(sum(i.amount for i in self.items), 2)

    @property
    def gutter_subtotal(self) -> float:
        return round(sum(i.amount for i in self.gutter_items), 2)

    @property
    def quoted_ex_gst(self) -> float:
        return round(self.internal_subtotal * (1 + self.markup_pct), 2)

    @property
    def grand_total_ex_gst(self) -> float:
        return round(self.quoted_ex_gst + self.gutter_subtotal, 2)

    @property
    def gst(self) -> float:
        return round(self.grand_total_ex_gst * GST_RATE, 2)

    @property
    def total_inc_gst(self) -> float:
        return round(self.grand_total_ex_gst + self.gst, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            'items': [i.__dict__ | {'amount': i.amount} for i in self.items],
            'gutter_items': [i.__dict__ | {'amount': i.amount} for i in self.gutter_items],
            'internal_subtotal': self.internal_subtotal,
            'markup_pct':        self.markup_pct,
            'quoted_ex_gst':     self.quoted_ex_gst,
            'gutter_subtotal':   self.gutter_subtotal,
            'grand_total_ex_gst':self.grand_total_ex_gst,
            'gst':               self.gst,
            'total_inc_gst':     self.total_inc_gst,
        }


def build_port_city_quote(
    *,
    # Roof core
    roof_type:      str   = 'hip',          # 'gable' | 'hip' | 'ultra' | 'ultra_gable' | 'ultra_hip'
    roof_area_m2:   float = 0,
    # Linear-metre items
    eave_lm:        float = 0,              # safety rail / edge protection
    perimeter_m:    float = 0,
    # Gutters
    include_gutters: bool = False,
    gutter_lm:      float = 0,
    downpipe_90mm:  int   = 0,
    downpipe_100mm: int   = 0,
    gutter_travel_days: float = 0,           # opt-in — extra travel days for gutter job
    # Fascia / Tiles / Battens
    include_fascia: bool  = False,
    tile_replace_m2:float = 0,
    batten_replace_lm: float = 0,
    # Roof condition / removal
    is_asbestos:    bool  = False,
    is_decromastic: bool  = False,
    is_highset:     bool  = False,          # 2+ storey adds highset allowances
    # Solar
    solar_panels_rr:int   = 0,              # Remove & Replace count
    solar_panels_remove:int = 0,            # Remove only count
    solar_hw_rr:    bool  = False,
    solar_hw_remove:bool  = False,
    solar_tube_rr:  bool  = False,
    skylight_count: int   = 0,
    # Site logistics
    include_fuse_pull: bool = False,        # opt-in — workbooks show ~70% of jobs
    include_bins:   bool  = False,          # opt-in — many jobs ARE NOT charged bins
    crane_hours:    float = 0,
    box_gutter_lump:float = 0,              # flat $ amount when box gutters are billed
    bullnose_m2:    float = 0,
    bullnose_sheets:int   = 0,
    # Travel
    address:        str = '',
    suburb:         str = '',
    postcode:       str = '',
    travel_days_override: float | None = None,
    # Markup
    markup_pct:     float = DEFAULT_MARKUP,
) -> PortCityQuote:
    """Build a PortCityQuote line-by-line from the inputs.

    Returns a ``PortCityQuote`` whose ``to_dict()`` is JSON-friendly.
    """
    q = PortCityQuote(markup_pct=markup_pct)

    # 1. Fuse pull — almost always 1, flat fee
    if include_fuse_pull:
        q.add('Fuse Pull (Ergon disconnect/reconnect)', 1, 'ea', FUSE_PULL_FLAT)

    # 2. Edge Protection (safety rail) — typically full eave perimeter
    if eave_lm > 0:
        q.add('Edge Protection — safety rail', eave_lm, 'lm', EDGE_PROTECTION_PER_LM)

    # 3. Main roof — by type
    roof_rate = ROOF_RATE_PER_M2.get(str(roof_type).lower(), ROOF_RATE_PER_M2['hip'])
    if roof_area_m2 > 0:
        type_label = {
            'gable':       'Gable',
            'hip':         'Hip',
            'ultra':       'Ultra',
            'ultra_gable': 'Ultra-Gable',
            'ultra_hip':   'Ultra-Hip',
        }.get(str(roof_type).lower(), 'Hip')
        q.add(f'Colorbond Roof Replacement — {type_label}',
              roof_area_m2, 'm²', roof_rate)

    # 4. Asbestos / Decromastic removal premium
    if is_asbestos:
        q.add('Asbestos Removal — Starting At', 1, 'lot', ASBESTOS_STARTING_AT)
        if is_highset:
            q.add('Asbestos — Highset Allowance', 1, 'lot', ASBESTOS_HIGHSET_ALLOW)
    if is_decromastic and roof_area_m2 > 0:
        q.add('Decromastic Tile Removal', roof_area_m2, 'm²', DECROMASTIC_PER_M2)

    # 5. Tile / Batten replacement (if applicable)
    if tile_replace_m2 > 0:
        q.add('Tile Replacement', tile_replace_m2, 'm²', TILE_REPLACE_PER_M2)
    if batten_replace_lm > 0:
        q.add('Batten Replacement', batten_replace_lm, 'lm', BATTEN_REPLACE_PER_LM)

    # 6. Bullnose / curving (verandah/patio)
    if bullnose_m2 > 0:
        q.add('Bullnose — .6 Curve Quality', bullnose_m2, 'm²', CURVE_QUALITY_PER_M2)
    if bullnose_sheets > 0:
        q.add('Curving Sheet labour', bullnose_sheets, 'sheet', CURVING_SHEET_RATE)

    # 7. Bins — 1 per 200 m² (round up), $1,600 each — opt-in
    if include_bins and roof_area_m2 > 0:
        bin_count = max(1, math.ceil(roof_area_m2 / 200))
        q.add(f'Skip Bins ({bin_count} × 200 m² capacity)',
              bin_count, 'ea', BINS_PER_200M2)

    # 7b. Box gutters — flat dollar amount when present (Oonoonba had $1,000)
    if box_gutter_lump > 0:
        q.add('Box Gutters', 1, 'lot', box_gutter_lump)

    # 8. Crane (if hours specified)
    if crane_hours > 0:
        q.add('Crane hire', crane_hours, 'hr', CRANE_PER_HR)

    # 9. Solar — panels, HW, tubes, skylights
    if solar_panels_rr > 0:
        q.add('Solar Panel — Remove & Replace',
              solar_panels_rr, 'ea', SOLAR_PANEL_RR)
        if is_highset:
            q.add('Solar — Highset Allowance', 1, 'lot', SOLAR_HIGHSET_ALLOW)
    if solar_panels_remove > 0:
        q.add('Solar Panel — Remove Only',
              solar_panels_remove, 'ea', SOLAR_PANEL_REMOVE)
    if solar_hw_rr:
        q.add('Solar Hot Water — Remove & Replace', 1, 'lot', SOLAR_HW_RR)
    if solar_hw_remove:
        q.add('Solar Hot Water — Remove Only', 1, 'lot', SOLAR_HW_REMOVE)
    if solar_tube_rr:
        q.add('Solar Tube System — Remove & Replace', 1, 'lot', SOLAR_TUBE_RR)
    if skylight_count > 0:
        q.add('Skylight — Remove & Replace',
              skylight_count, 'ea', SKYLIGHT_RR)

    # 10. Travel — by zone (Ayr/Charters/Cairns/Mackay)
    zone, days_default, daily_rate = detect_travel_zone(
        address=address, suburb=suburb, postcode=postcode,
    )
    days = travel_days_override if travel_days_override is not None else days_default
    if days and daily_rate > 0:
        nice_zone = zone.replace('_', '/').title()
        q.add(f'Travel — {nice_zone}', days, 'day', daily_rate)

    # 11. Fascia covers (rare — only when toggled)
    if include_fascia and eave_lm > 0:
        q.add('Fascia Covers', eave_lm, 'lm', FASCIA_COVER_PER_LM)

    # ── Gutter sub-quote (separate rollup) ─────────────────────────────────
    if include_gutters and gutter_lm > 0:
        q.add('Guttering — Colorbond 150 mm quad',
              gutter_lm, 'lm', GUTTER_PER_LM, gutter=True)
        if downpipe_90mm > 0:
            q.add('Downpipes — 90 mm PVC',
                  downpipe_90mm, 'ea', DOWNPIPE_90MM, gutter=True)
        if downpipe_100mm > 0:
            q.add('Downpipes — 100 mm PVC',
                  downpipe_100mm, 'ea', DOWNPIPE_100MM, gutter=True)
        # Optional gutter-only travel days — only if explicitly specified
        if gutter_travel_days > 0 and daily_rate > 0:
            q.add(f'Gutter Travel — {zone.replace("_","/").title()}',
                  gutter_travel_days, 'day', daily_rate, gutter=True)

    return q
