"""Seed UC2 Didi / Dulong Downs with realistic initial data."""
from datetime import date
from django.core.management.base import BaseCommand
from uc2_didi.models import (
    RefBudget, RefCategory, RefZone, Metadata,
    ActionHub, Budget, Cashflow, Decision, Document,
    Procurement, ProjectPhase, ProjectPlan, RoomMatrix,
    Vendor, Hypothesis, LearningRule
)


class Command(BaseCommand):
    help = 'Seed UC2 Didi/Dulong Downs with initial data'

    def handle(self, *args, **options):
        self.stdout.write('Seeding UC2 Didi — Dulong Downs...')
        self._seed_refs()
        self._seed_learning_rules()
        self._seed_phases()
        self._seed_budget()
        self._seed_actions()
        self._seed_vendors()
        self._seed_decisions()
        self._seed_cashflows()
        self._seed_procurement()
        self._seed_documents()
        self._seed_metadata()
        self.stdout.write(self.style.SUCCESS('UC2 seeding complete!'))

    def _seed_refs(self):
        cats = ['Structure','Electrical','Plumbing','Joinery','Painting','Flooring',
                'Roofing','Landscaping','External Works','Fit-Out','Admin']
        for c in cats:
            RefCategory.objects.get_or_create(name=c, defaults={'type': 'Trade'})

        zones = [
            ('Main Residence', 'Primary dwelling', 280.0),
            ('Pool House', 'Pool area and cabana', 65.0),
            ('Garage', 'Double garage + workshop', 90.0),
            ('Landscaping', 'Gardens and grounds', 1500.0),
            ('External', 'Driveway, paths, fencing', 800.0),
        ]
        for name, desc, area in zones:
            RefZone.objects.get_or_create(name=name, defaults={'description': desc, 'area_sqm': area})

        budgets = [
            ('Foundation & Slab', 185000),
            ('Framing & Structure', 220000),
            ('Roofing', 95000),
            ('Electrical', 75000),
            ('Plumbing', 68000),
            ('Joinery & Cabinetry', 145000),
            ('Painting & Render', 62000),
            ('Flooring', 85000),
            ('External & Landscaping', 120000),
            ('Fit-Out & Fixtures', 95000),
            ('Contingency', 70000),
        ]
        for cat, amt in budgets:
            RefBudget.objects.get_or_create(category=cat, defaults={'budget_allocated': amt})
        self.stdout.write('  ✓ Reference tables seeded')

    def _seed_learning_rules(self):
        rules = [
            ('LRN-0001', 'Always load CHANGE_LOG on session start to detect off-system changes', 'Session', False),
            ('LRN-0006', 'Never process more than one bulk payment batch per session without re-verifying CASHFLOWS', 'Finance', True),
            ('LRN-0010', 'Claudia Salem has authority to approve structural changes; Paul Sakrzewski for schedule changes', 'Governance', False),
            ('LRN-0015', 'All decisions affecting budget >$10,000 require dual confirmation (Claudia AND Paul)', 'Finance', True),
            ('LRN-0018', 'Do not write to PROJECT_PHASES without explicit confirmation from the Project Manager', 'Governance', True),
            ('LRN-0020', 'When ACTION_HUB items are overdue >7 days, proactively flag at session start', 'Session', False),
            ('LRN-0024', 'Vendor LEARNING_RULES entries for Lighthouse Noosa require Claudia Salem sign-off', 'Vendor', True),
            ('LRN-0030', 'Always cross-check CASHFLOWS before any Lighthouse Noosa invoice write (overpayment protection)', 'Finance', False),
            ('LRN-0032', 'Roof framing completion percentage cannot exceed 100% — enforce hard cap', 'Validation', True),
        ]
        for code, desc, cat, cant_override in rules:
            LearningRule.objects.get_or_create(
                rule_code=code,
                defaults={'description': desc, 'category': cat,
                          'cannot_override': cant_override, 'is_active': True}
            )
        self.stdout.write(f'  ✓ Learning rules seeded')

    def _seed_phases(self):
        phases = [
            (1, 'Site Preparation & Demolition', 'complete', date(2025, 9, 1), date(2025, 10, 15), 100, 45000),
            (2, 'Foundation & Slab', 'complete', date(2025, 10, 16), date(2025, 12, 10), 100, 185000),
            (3, 'Framing & Structure', 'in_progress', date(2026, 1, 6), date(2026, 4, 30), 78, 220000),
            (4, 'Roofing', 'in_progress', date(2026, 4, 1), date(2026, 5, 31), 35, 95000),
            (5, 'Electrical Rough-In', 'in_progress', date(2026, 3, 15), date(2026, 6, 15), 40, 75000),
            (6, 'Plumbing Rough-In', 'not_started', date(2026, 5, 1), date(2026, 7, 31), 0, 68000),
            (7, 'External Cladding & Render', 'not_started', date(2026, 6, 1), date(2026, 8, 31), 0, 62000),
            (8, 'Joinery & Internal Fit-Out', 'not_started', date(2026, 7, 1), date(2026, 10, 31), 0, 145000),
            (9, 'Flooring & Painting', 'not_started', date(2026, 9, 1), date(2026, 11, 30), 0, 147000),
            (10, 'Landscaping & External', 'not_started', date(2026, 10, 1), date(2027, 1, 31), 0, 120000),
            (11, 'Practical Completion', 'not_started', date(2027, 1, 15), date(2027, 2, 28), 0, 20000),
        ]
        for order, name, status, start, end, pct, budget in phases:
            ProjectPhase.objects.get_or_create(
                name=name,
                defaults={'status': status, 'start_date': start, 'end_date': end,
                          'completion_pct': pct, 'budget_estimate': budget, 'order': order}
            )
        self.stdout.write(f'  ✓ Project phases seeded')

    def _seed_budget(self):
        data = [
            ('Foundation & Slab', 'P2', 'Slab pour — main residence', 185000, 187400, 0),
            ('Framing & Structure', 'P3', 'Timber frame — main dwelling', 220000, 228400, 0),
            ('Roofing', 'P4', 'Colorbond roof — main + pool house', 95000, 0, 33250),
            ('Electrical', 'P5', 'Electrical rough-in all zones', 75000, 30000, 0),
            ('Plumbing', 'P6', 'Plumbing rough-in', 68000, 0, 0),
            ('Joinery & Cabinetry', 'P8', 'Kitchen, bathrooms, wardrobes', 145000, 0, 52000),
            ('Painting & Render', 'P7', 'External render + internal painting', 62000, 0, 0),
            ('Flooring', 'P9', 'Timber flooring + tiles', 85000, 0, 0),
            ('External & Landscaping', 'P10', 'Landscaping, driveway, pool', 120000, 0, 0),
            ('Fit-Out & Fixtures', 'P8', 'Fixtures, fittings, appliances', 95000, 0, 0),
            ('Contingency', 'ALL', '10% contingency reserve', 70000, 15800, 0),
        ]
        for cat_name, phase, desc, est, actual, committed in data:
            try:
                cat = RefBudget.objects.get(category=cat_name)
                Budget.objects.get_or_create(
                    category=cat, phase=phase, description=desc,
                    defaults={'estimated': est, 'actual': actual, 'committed': committed}
                )
            except RefBudget.DoesNotExist:
                pass
        self.stdout.write(f'  ✓ Budget seeded')

    def _seed_actions(self):
        actions = [
            ('Complete electrical rough-in — master bedroom wing', 'Jack Henderson', date(2026, 5, 8), 'overdue', 'critical'),
            ('Finalise roof tile colour selection with Colorbond rep', 'Claudia Salem', date(2026, 5, 15), 'open', 'high'),
            ('Sign off on framing engineer inspection report', 'Paul Sakrzewski', date(2026, 5, 12), 'overdue', 'high'),
            ('Place order for Caesarstone benchtops — lead time 8 weeks', 'Claudia Salem', date(2026, 5, 20), 'open', 'medium'),
            ('Confirm pool house slab dimensions with engineer', 'Jack Henderson', date(2026, 5, 25), 'open', 'medium'),
            ('Review and approve joinery drawings', 'Claudia Salem', date(2026, 5, 28), 'open', 'high'),
            ('Obtain building certification for Phase 2 completion', 'Paul Sakrzewski', date(2026, 5, 10), 'overdue', 'critical'),
            ('Schedule plumbing contractor site visit', 'Jack Henderson', date(2026, 5, 30), 'open', 'medium'),
            ('Update project insurance for Phase 3 completion value', 'Claudia Salem', date(2026, 5, 31), 'open', 'low'),
            ('Arrange professional photos of framing progress', 'Claudia Salem', date(2026, 6, 15), 'open', 'low'),
        ]
        for action, owner, due, status, priority in actions:
            ActionHub.objects.get_or_create(
                action=action,
                defaults={'owner': owner, 'due_date': due, 'status': status, 'priority': priority}
            )
        self.stdout.write(f'  ✓ Action Hub seeded')

    def _seed_vendors(self):
        vendors = [
            ('Lighthouse Noosa', 'Concrete', 'Mark Thompson', 'mark@lighthousenoosa.com.au', '0412 111 222', 8.5),
            ('Sunshine Coast Electrical', 'Electrical', 'Dave Richards', 'dave@scelec.com.au', '0423 222 333', 9.0),
            ('Noosa Plumbing Co', 'Plumbing', 'Chris Walker', 'chris@noosaplumbing.com.au', '0434 333 444', 8.0),
            ('Tewantin Timber Frames', 'Framing', 'Rob Johnson', 'rob@tewantintimber.com.au', '0445 444 555', 9.2),
            ('Colorbond Direct QLD', 'Roofing', 'Sarah Lee', 'sarah@colorbondqld.com.au', '0456 555 666', 8.8),
            ('Caesarstone Surfaces', 'Joinery', 'Emma Chen', 'emma@caesarstone.com.au', '0467 666 777', 9.5),
            ('Coolum Landscaping', 'Landscaping', 'Tom Baker', 'tom@coolumlandscaping.com.au', '0478 777 888', 8.2),
            ('Noosa Hire & Equipment', 'Equipment', 'Phil Grant', 'phil@noosahire.com.au', '0489 888 999', 7.8),
        ]
        for name, cat, contact, email, phone, rating in vendors:
            Vendor.objects.get_or_create(
                name=name,
                defaults={'category': cat, 'contact': contact, 'email': email,
                          'phone': phone, 'rating': rating, 'is_active': True}
            )
        self.stdout.write(f'  ✓ Vendors seeded')

    def _seed_decisions(self):
        decisions = [
            ('Switch from rendered block to Scyon Axon cladding on pool house', 'Claudia Salem', date(2026, 3, 15), 'confirmed', 'Cost saving of ~$12,000 and faster installation timeline'),
            ('Upgrade kitchen benchtops from laminate to Caesarstone throughout', 'Claudia Salem & Jack Henderson', date(2026, 4, 2), 'confirmed', 'Long-term value; aligned with luxury finish intent'),
            ('Use Colorbond Shale Grey for main roof instead of Surfmist', 'Claudia Salem', date(2026, 4, 18), 'confirmed', 'Better thermal performance and contemporary aesthetic'),
            ('Defer pool construction to after practical completion', 'Jack Henderson', date(2026, 2, 10), 'confirmed', 'Cash flow management — separate contract post-PC'),
            ('Retain existing mature fig trees on eastern boundary', 'Claudia Salem', date(2026, 1, 20), 'confirmed', 'Arborist report confirmed trees add significant property value'),
        ]
        for desc, by, dt, status, rationale in decisions:
            Decision.objects.get_or_create(
                description=desc,
                defaults={'made_by': by, 'date': dt, 'status': status, 'rationale': rationale}
            )
        self.stdout.write(f'  ✓ Decisions seeded')

    def _seed_cashflows(self):
        cashflows = [
            ('2025-09', 45000, 45000),
            ('2025-10', 95000, 92500),
            ('2025-11', 90000, 94800),
            ('2025-12', 110000, 107200),
            ('2026-01', 125000, 128400),
            ('2026-02', 115000, 118900),
            ('2026-03', 135000, 131200),
            ('2026-04', 140000, 143600),
            ('2026-05', 130000, 0),
            ('2026-06', 145000, 0),
            ('2026-07', 160000, 0),
            ('2026-08', 155000, 0),
        ]
        for period, projected, actual in cashflows:
            Cashflow.objects.get_or_create(period=period, defaults={'projected': projected, 'actual': actual})
        self.stdout.write(f'  ✓ Cashflows seeded')

    def _seed_procurement(self):
        items = [
            ('Colorbond Shale Grey roofing sheets — main dwelling 280m²', 'Colorbond Direct QLD', 280, 42.0, 'ordered', date(2026, 5, 20)),
            ('Caesarstone Cloudburst benchtops — kitchen + bathrooms', 'Caesarstone Surfaces', 1, 18500.0, 'pending', date(2026, 6, 15)),
            ('Scyon Axon cladding boards 3600mm — pool house', 'Colorbond Direct QLD', 120, 68.0, 'delivered', date(2026, 4, 10)),
            ('Structural steel — header beams main entry', 'Tewantin Timber Frames', 6, 850.0, 'invoiced', date(2026, 3, 28)),
            ('Main electrical switchboard — 3-phase 80A', 'Sunshine Coast Electrical', 1, 4200.0, 'delivered', date(2026, 3, 15)),
            ('Timber flooring — Spotted Gum 130mm', 'Coolum Landscaping', 320, 95.0, 'pending', date(2026, 8, 1)),
        ]
        for item, vendor, qty, price, status, due in items:
            Procurement.objects.get_or_create(
                item=item,
                defaults={'vendor_name': vendor, 'quantity': qty, 'unit_price': price,
                          'total': qty * price, 'status': status, 'due_date': due}
            )
        self.stdout.write(f'  ✓ Procurement seeded')

    def _seed_documents(self):
        docs = [
            ('Building Approval DA-2025-4471', 'Permit', '1.0', date(2025, 8, 15)),
            ('Structural Engineering Report — Slab & Footings', 'Engineering', '2.1', date(2025, 10, 2)),
            ('Architectural Plans — Rev D', 'Plans', 'D', date(2026, 1, 10)),
            ('Electrical Design Specification', 'Specification', '1.0', date(2026, 2, 5)),
            ('Soil Classification Report', 'Report', '1.0', date(2025, 7, 20)),
            ('Contract — Lighthouse Noosa (concrete supply)', 'Contract', '1.0', date(2025, 10, 1)),
        ]
        for name, doc_type, version, upload_date in docs:
            Document.objects.get_or_create(
                name=name,
                defaults={'doc_type': doc_type, 'version': version, 'upload_date': upload_date}
            )
        self.stdout.write(f'  ✓ Documents seeded')

    def _seed_metadata(self):
        data = {
            'project_name': 'Dulong Downs Residence',
            'client': 'Jack Henderson / Aboda Investments Pty Ltd',
            'site_address': '199 Dulong Road, Dulong QLD 4560',
            'da_number': 'DA-2025-4471',
            'builder': 'P&P Building Group',
            'contract_value': '1,220,000',
            'target_completion': '2027-02-28',
            'didi_mailbox': 'didi@dulongdowns.com.au',
            'authorised_senders': 'claudia@dulongdowns.com.au, paul@ppbuilding.com.au',
        }
        for key, value in data.items():
            Metadata.objects.get_or_create(key=key, defaults={'value': value})
        self.stdout.write(f'  ✓ Metadata seeded')
