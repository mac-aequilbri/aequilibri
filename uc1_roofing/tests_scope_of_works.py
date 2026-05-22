"""Verify scope-of-works output matches Port City's PDF format.

Run:
  ./venv/Scripts/python.exe -m uc1_roofing.tests_scope_of_works
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from uc1_roofing.pricing_port_city import (build_scope_of_works, build_job_notes)


def show(label, scope, job_notes):
    print(f"\n{'='*70}\n  {label}\n{'='*70}")
    for i, line in enumerate(scope, start=1):
        print(f"  {i}. {line}")
    print(f"\n  Job Notes: {job_notes}")


# 1. Standard ColorBond replacement, NO gutters in quote, NO extras
show('Standard ColorBond replacement (gutters NOT included)',
     build_scope_of_works(include_gutters=False),
     build_job_notes(include_gutters=False))

# 2. ColorBond with gutters folded in
show('ColorBond + Gutters INCLUDED',
     build_scope_of_works(include_gutters=True),
     build_job_notes(include_gutters=True))

# 3. Hilleard's actual job (Rangewood): bullnose + 36 solar panels + gutters
show('Hilleard (Rangewood) — bullnose + 36 solar + gutters',
     build_scope_of_works(
         bullnose_m2=12,
         solar_panels_rr=36,
         include_gutters=True),
     build_job_notes(include_gutters=True))

# 4. Bullard (Mysterton): remove solar HW only
show('Bullard (Mysterton) — Remove solar HW only',
     build_scope_of_works(solar_hw_remove=True, include_gutters=False),
     build_job_notes(include_gutters=False))

# 5. Ward (West End) — ASBESTOS roof, gutters additional
show('Ward (West End) — ASBESTOS roof',
     build_scope_of_works(is_asbestos=True, include_gutters=False),
     build_job_notes(is_asbestos=True, include_gutters=False))

# 6. Lynham (Douglas) — Decromastic + skylight
show('Lynham (Douglas) — Decromastic + skylight',
     build_scope_of_works(is_decromastic=True, skylight_count=1,
                          include_gutters=False),
     build_job_notes(include_gutters=False))
