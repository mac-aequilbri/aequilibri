"""
Management command: check_vendor_prices

Fetches current pricing from each vendor's source URL and compares
against the price stored in the database.  If a change is detected,
the DB record is updated and the change is logged in PriceCheckLog.

How it works
────────────
1. For each active VendorMaterialPrice that has a price_source_url:
   - Fetch the page via requests (GET, 10 s timeout, spoofed UA)
   - Run a set of heuristic price extractors (regex patterns known to
     appear on Australian building supplier pages)
   - If a price is found and differs from the DB value by > THRESHOLD %,
     update the record and record the delta

2. For prices with no source URL (or where the fetch/parse fails):
   - Mark as "checked" with the current timestamp but leave price unchanged
   - Count as "unchanged" in the log

3. Write a PriceCheckLog entry summarising the run.

Price-change threshold
──────────────────────
By default a price is only updated when the new value differs by more
than CHANGE_THRESHOLD_PCT (2 %).  This filters out minor page variations
(e.g. GST-inclusive vs exclusive inconsistencies on some pages).

Usage
─────
    python manage.py check_vendor_prices
    python manage.py check_vendor_prices --dry-run      # report only, no writes
    python manage.py check_vendor_prices --vendor "Stratco"
"""
import re
import time
import traceback
from decimal import Decimal
from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from uc1_roofing.models import VendorMaterialPrice, PriceCheckLog

# ── Config ────────────────────────────────────────────────────────────────────
CHANGE_THRESHOLD_PCT = 2.0   # ignore changes smaller than this %
REQUEST_DELAY_S      = 1.5   # polite delay between vendor requests
REQUEST_TIMEOUT_S    = 10

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (compatible; AequilibriPriceBot/1.0; '
        '+https://aequilibri.ai/price-check)'
    ),
    'Accept': 'text/html,application/xhtml+xml',
}

# ── Price extraction patterns ─────────────────────────────────────────────────
# Ordered by specificity — first match wins.
# All must produce a single capturing group containing digits (and optionally a dot).
PRICE_PATTERNS = [
    # "$28.50 / m²" or "$28.50/m²" or "$28.50 per m²"
    r'\$\s*(\d{1,4}(?:\.\d{1,2})?)\s*(?:/|per)\s*m',
    # "$28.50 ex GST" or "28.50 ex-GST"
    r'\$?\s*(\d{1,4}(?:\.\d{1,2})?)\s*ex[\s\-]?gst',
    # "from $28.50"
    r'from\s+\$\s*(\d{1,4}(?:\.\d{1,2})?)',
    # Generic dollar amount on a product page: "$28.50" isolated near price context
    r'price[^$\d]{0,30}\$\s*(\d{1,4}(?:\.\d{1,2})?)',
    r'cost[^$\d]{0,30}\$\s*(\d{1,4}(?:\.\d{1,2})?)',
    r'rate[^$\d]{0,30}\$\s*(\d{1,4}(?:\.\d{1,2})?)',
]


def _try_fetch_price(url: str) -> tuple[Decimal | None, str]:
    """
    Attempt to scrape a price from `url`.
    Returns (price_decimal_or_None, status_message).
    """
    try:
        import urllib.request
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            html = resp.read().decode('utf-8', errors='replace').lower()
    except Exception as exc:
        return None, f'fetch_error: {exc.__class__.__name__}: {exc}'

    for pattern in PRICE_PATTERNS:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            try:
                price = Decimal(match.group(1))
                if Decimal('1') <= price <= Decimal('9999'):
                    return price, f'matched pattern: {pattern[:40]}'
            except Exception:
                continue

    return None, 'no_price_found — page loaded but no price pattern matched'


def _pct_change(old: Decimal, new: Decimal) -> float:
    if old == 0:
        return 0.0
    return float(abs(new - old) / old * 100)


class Command(BaseCommand):
    help = 'Fetch latest prices from vendor source URLs and update the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would change without writing to the database',
        )
        parser.add_argument(
            '--vendor', type=str, default='',
            help='Only check prices for vendors whose name contains this string',
        )

    def handle(self, *args, **options):
        dry_run     = options['dry_run']
        vendor_filter = options['vendor'].strip().lower()

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no database writes'))

        qs = (
            VendorMaterialPrice.objects
            .filter(is_available=True, vendor__is_active=True)
            .select_related('vendor')
            .order_by('vendor__name', 'material')
        )
        if vendor_filter:
            qs = qs.filter(vendor__name__icontains=vendor_filter)

        vendors_seen   = set()
        updated        = 0
        unchanged      = 0
        errors         = 0
        log_lines      = []
        change_lines   = []

        current_vendor = None

        for vmp in qs:
            if vmp.vendor.name != current_vendor:
                current_vendor = vmp.vendor.name
                vendors_seen.add(current_vendor)
                self.stdout.write(f'\n── {current_vendor}')
                time.sleep(REQUEST_DELAY_S)

            label = f'  {vmp.get_material_display():20s} [{vmp.item_code}]'

            if not vmp.price_source_url:
                self.stdout.write(f'{label}  no source URL — skipped')
                log_lines.append(f'{label}  SKIP (no source URL)')
                unchanged += 1
                if not dry_run:
                    VendorMaterialPrice.objects.filter(pk=vmp.pk).update(
                        last_verified=timezone.now()
                    )
                continue

            fetched_price, fetch_msg = _try_fetch_price(vmp.price_source_url)

            if fetched_price is None:
                self.stdout.write(
                    self.style.WARNING(f'{label}  WARN: {fetch_msg}')
                )
                log_lines.append(f'{label}  WARN: {fetch_msg}')
                errors += 1
                # Still stamp last_verified so we know the check ran
                if not dry_run:
                    VendorMaterialPrice.objects.filter(pk=vmp.pk).update(
                        last_verified=timezone.now()
                    )
                continue

            pct = _pct_change(vmp.unit_price_ex_gst, fetched_price)

            if pct < CHANGE_THRESHOLD_PCT:
                self.stdout.write(
                    f'{label}  ${vmp.unit_price_ex_gst} → ${fetched_price}  (no change, Δ{pct:.1f}%)'
                )
                log_lines.append(
                    f'{label}  UNCHANGED  ${vmp.unit_price_ex_gst} (fetched ${fetched_price}, Δ{pct:.1f}%)'
                )
                unchanged += 1
                if not dry_run:
                    VendorMaterialPrice.objects.filter(pk=vmp.pk).update(
                        last_verified=timezone.now()
                    )
            else:
                direction = '▲' if fetched_price > vmp.unit_price_ex_gst else '▼'
                self.stdout.write(self.style.SUCCESS(
                    f'{label}  {direction} ${vmp.unit_price_ex_gst} → ${fetched_price}  (Δ{pct:.1f}%)'
                ))
                change_line = (
                    f'{vmp.vendor.name} / {vmp.get_material_display()}: '
                    f'${vmp.unit_price_ex_gst} → ${fetched_price} ({direction} {pct:.1f}%)'
                )
                log_lines.append(f'{label}  UPDATED  {change_line}')
                change_lines.append(change_line)
                updated += 1

                if not dry_run:
                    VendorMaterialPrice.objects.filter(pk=vmp.pk).update(
                        previous_price=vmp.unit_price_ex_gst,
                        unit_price_ex_gst=fetched_price,
                        last_verified=timezone.now(),
                    )

        # ── Summary ──────────────────────────────────────────────────────────
        self.stdout.write('\n' + '─' * 60)
        self.stdout.write(
            f'Checked {len(vendors_seen)} vendor(s) | '
            f'{updated} price(s) updated | '
            f'{unchanged} unchanged | '
            f'{errors} error(s)'
        )

        if change_lines:
            self.stdout.write('\nPrice changes this run:')
            for cl in change_lines:
                self.stdout.write(f'  • {cl}')
        elif not errors:
            self.stdout.write('All prices are current — no changes detected.')

        if dry_run:
            self.stdout.write(self.style.WARNING('\n(dry-run: nothing written to DB)'))
            return

        # ── Write PriceCheckLog ───────────────────────────────────────────────
        if updated > 0:
            status = 'partial' if errors else 'success'
        elif errors and not unchanged and not updated:
            status = 'error'
        elif not updated and not errors:
            status = 'no_change'
        else:
            status = 'partial'

        summary_text = (
            f'{updated} price(s) updated, {unchanged} unchanged, {errors} error(s). '
            + (('Changes: ' + ' | '.join(change_lines)) if change_lines else 'No price movements.')
        )

        PriceCheckLog.objects.create(
            status=status,
            vendors_checked=len(vendors_seen),
            prices_updated=updated,
            prices_unchanged=unchanged,
            errors=errors,
            summary=summary_text,
            raw_log='\n'.join(log_lines),
        )

        self.stdout.write(self.style.SUCCESS('\nPrice check log saved.'))
