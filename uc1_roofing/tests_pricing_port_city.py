"""Validation: replicate the 5 Estimating Calculation worksheets.

Run from the repo root:
  ./venv/Scripts/python.exe -m uc1_roofing.tests_pricing_port_city
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from uc1_roofing.pricing_port_city import build_port_city_quote


def report(label, expected_internal, expected_quoted, expected_gutter, **kw):
    q = build_port_city_quote(**kw)
    d = q.to_dict()
    print(f"\n{'='*72}\n  {label}\n{'='*72}")
    for i in d['items']:
        print(f"  {i['description']:50s} {i['quantity']:>6} {i['unit']:>4} x ${i['rate']:>7.2f} = ${i['amount']:>10.2f}")
    if d['gutter_items']:
        print('  --- gutter ---')
        for i in d['gutter_items']:
            print(f"  {i['description']:50s} {i['quantity']:>6} {i['unit']:>4} x ${i['rate']:>7.2f} = ${i['amount']:>10.2f}")
    int_ok = abs(d['internal_subtotal']  - expected_internal) < 1
    qot_ok = abs(d['quoted_ex_gst']      - expected_quoted)   < 1
    gut_ok = abs(d['gutter_subtotal']    - expected_gutter)   < 1
    print(f"  Internal:  ${d['internal_subtotal']:>10.2f}   (expected ${expected_internal:>10.2f}) {'OK' if int_ok else 'XX MISMATCH'}")
    print(f"  Quoted:    ${d['quoted_ex_gst']:>10.2f}   (expected ${expected_quoted:>10.2f}) {'OK' if qot_ok else 'XX MISMATCH'}")
    print(f"  Gutters:   ${d['gutter_subtotal']:>10.2f}   (expected ${expected_gutter:>10.2f}) {'OK' if gut_ok else 'XX MISMATCH'}")
    print(f"  Total inc GST: ${d['total_inc_gst']:>10.2f}")
    return int_ok and qot_ok and gut_ok


passed = 0
total = 0

# 1. Condron Place AYR (#593) — Gable 171, rail 35, 3d Ayr, fuse pull,
#    gutters 23 lm + 1d gutter travel
total += 1
if report('#593 11 Condron Place AYR',
          expected_internal=23485, expected_quoted=25833.5, expected_gutter=2900,
          roof_type='gable', roof_area_m2=171, eave_lm=35,
          suburb='Ayr', travel_days_override=3,
          include_fuse_pull=True, include_bins=False,
          include_gutters=True, gutter_lm=23, gutter_travel_days=1):
    passed += 1

# 2. Toomulla Esplanade — Ultra-Gable 105, rail 30, 1d Ayr travel, fuse pull,
#    gutters 31 lm + 4x90mm downpipes, NO gutter travel
total += 1
if report('4 The Esplanade TOOMULLA',
          expected_internal=16370, expected_quoted=18007, expected_gutter=4100,
          roof_type='ultra_gable', roof_area_m2=105, eave_lm=30,
          suburb='Toomulla', travel_days_override=1,
          include_fuse_pull=True, include_bins=False,
          include_gutters=True, gutter_lm=31, downpipe_90mm=4):
    passed += 1

# 3. West End — Hip 125, rail 36, no travel, fuse pull, solar HW R&R, gutters 40
total += 1
if report('30 Estate Street WEST END',
          expected_internal=19234, expected_quoted=21157.4, expected_gutter=4000,
          roof_type='hip', roof_area_m2=125, eave_lm=36,
          suburb='West End',
          include_fuse_pull=True, include_bins=False,
          solar_hw_rr=True,
          include_gutters=True, gutter_lm=40):
    passed += 1

# 4. Oonoonba — Hip 180, rail 45, no travel, fuse pull, 18 solar R&R + highset,
#    box gutters $1000, gutters 65
total += 1
if report('112 Abbott Street OONOONBA',
          expected_internal=28273, expected_quoted=31100.3, expected_gutter=6500,
          roof_type='hip', roof_area_m2=180, eave_lm=45,
          suburb='Oonoonba',
          include_fuse_pull=True, include_bins=False,
          solar_panels_rr=18, is_highset=True,
          box_gutter_lump=1000,
          include_gutters=True, gutter_lm=65):
    passed += 1

# 5. Mt Louisa — Hip 340, rail 90, no travel, NO fuse pull, NO bins, no gutters
total += 1
if report('14 Calliandra Court MT LOUISA',
          expected_internal=45910, expected_quoted=50501, expected_gutter=0,
          roof_type='hip', roof_area_m2=340, eave_lm=90,
          suburb='Mt Louisa',
          include_fuse_pull=False, include_bins=False):
    passed += 1

print(f"\n{'='*72}\n  RESULT: {passed}/{total} workbooks match exactly\n{'='*72}")
