"""Seed UC3 MSME Project Coordinator with multi-tenant demo data."""
from datetime import date
from django.core.management.base import BaseCommand
from uc3_msme.models import (Tenant, Project, Phase, ActionItem, Risk,
                              Budget, Vendor, Decision, Document, Cashflow, TenantUser)


class Command(BaseCommand):
    help = 'Seed UC3 MSME Coordinator with multi-tenant demo data'

    def handle(self, *args, **options):
        self.stdout.write('Seeding UC3 MSME Coordinator...')
        t1 = self._seed_tenant1()
        t2 = self._seed_tenant2()
        self.stdout.write(self.style.SUCCESS('UC3 seeding complete!'))

    def _seed_tenant1(self):
        """Tenant 1: Sunshine Coast Builds Pty Ltd"""
        tenant, _ = Tenant.objects.get_or_create(
            name='Sunshine Coast Builds',
            defaults={'org_id': 'org_scb_demo_001', 'is_active': True}
        )
        self.stdout.write(f'  ✓ Tenant: {tenant.name}')

        # Users
        users = [
            ('owner@scbuilds.com.au', 'James Kowalski', 'admin'),
            ('pm@scbuilds.com.au', 'Sarah Thompson', 'pm'),
            ('site@scbuilds.com.au', 'Mike Dawson', 'supervisor'),
            ('finance@scbuilds.com.au', 'Amanda Chen', 'finance'),
        ]
        for email, name, role in users:
            TenantUser.objects.get_or_create(tenant=tenant, email=email,
                defaults={'name': name, 'role': role, 'mfa_enabled': role in ('admin','pm','finance')})

        # Vendors
        for name, cat, rating in [
            ('Noosa Concrete', 'Concrete', 8), ('SC Electricals', 'Electrical', 9),
            ('Maroochydore Plumbing', 'Plumbing', 7), ('Caloundra Frames', 'Framing', 9),
        ]:
            Vendor.objects.get_or_create(tenant=tenant, name=name, defaults={'category': cat, 'rating': rating})

        # Project 1
        p1, _ = Project.objects.get_or_create(
            tenant=tenant, name='Bokarina Beach Duplex',
            defaults={'client': 'Henderson Holdings', 'status': 'active',
                      'start_date': date(2026, 1, 15), 'end_date': date(2026, 12, 31),
                      'health_score': 72, 'description': 'Luxury duplex — 4 bed / 3 bath each side'}
        )
        self._seed_phases(tenant, p1, [
            (1, 'Site Prep & Earthworks', 'complete', 100, date(2026,1,15), date(2026,2,15)),
            (2, 'Slab & Foundation', 'complete', 100, date(2026,2,16), date(2026,3,31)),
            (3, 'Framing', 'in_progress', 80, date(2026,4,1), date(2026,5,31)),
            (4, 'Roofing', 'in_progress', 25, date(2026,5,1), date(2026,6,30)),
            (5, 'Electrical & Plumbing Rough-In', 'not_started', 0, date(2026,6,1), date(2026,8,31)),
            (6, 'Internal Fit-Out', 'not_started', 0, date(2026,8,1), date(2026,11,30)),
            (7, 'Practical Completion', 'not_started', 0, date(2026,12,1), date(2026,12,31)),
        ])
        self._seed_actions(tenant, p1, [
            ('Order fascia and barge boards — Site 1 framing nearing completion', 'Mike Dawson', date(2026,5,14), 'overdue', 'high'),
            ('Confirm electrical rough-in drawings with designer', 'Sarah Thompson', date(2026,5,20), 'open', 'high'),
            ('Site safety audit — Q2 2026', 'Mike Dawson', date(2026,5,28), 'open', 'medium'),
            ('Review progress claim #3 with client', 'James Kowalski', date(2026,5,25), 'open', 'critical'),
            ('Order plumbing fixtures — 12-week lead time', 'Sarah Thompson', date(2026,6,1), 'open', 'high'),
            ('Arrange frame inspection with certifier', 'Mike Dawson', date(2026,5,18), 'overdue', 'critical'),
        ])
        self._seed_risks(tenant, p1, [
            ('Wet weather delays to framing completion', 3, 3, 'Monitor BOM 7-day forecast; have weather cover available'),
            ('Subcontractor availability for electrical rough-in phase', 4, 2, 'Pre-book SC Electricals 6 weeks ahead'),
            ('Timber frame price increase due to supply chain', 2, 4, 'Lock in remaining timber order this week'),
            ('Dispute with neighbours over boundary fence during earthworks', 1, 3, 'Engage surveyor to confirm boundary pegs'),
        ])
        self._seed_budget(tenant, p1, [
            (None, 'Site Preparation', 42000, 42000),
            (None, 'Concrete & Slab', 185000, 192400),
            (None, 'Framing', 210000, 0),
            (None, 'Roofing', 88000, 0),
            (None, 'Electrical', 72000, 0),
            (None, 'Plumbing', 65000, 0),
            (None, 'Internal Fit-Out', 320000, 0),
            (None, 'Contingency (10%)', 98200, 18500),
        ])

        # Project 2 (planning stage)
        p2, _ = Project.objects.get_or_create(
            tenant=tenant, name='Caloundra Townhouse — 3 Pack',
            defaults={'client': 'Caloundra Developments', 'status': 'planning',
                      'start_date': date(2026, 7, 1), 'end_date': date(2027, 9, 30),
                      'health_score': 85, 'description': '3 x 3-bed townhouses — DA approval obtained'}
        )
        self._seed_phases(tenant, p2, [
            (1, 'Pre-Construction & Setup', 'not_started', 0, date(2026,7,1), date(2026,8,31)),
            (2, 'Slab & Foundation', 'not_started', 0, date(2026,8,1), date(2026,9,30)),
            (3, 'Construction', 'not_started', 0, date(2026,9,1), date(2027,6,30)),
            (4, 'Fit-Out & Completion', 'not_started', 0, date(2027,6,1), date(2027,9,30)),
        ])
        self._seed_actions(tenant, p2, [
            ('Finalise construction program and milestone schedule', 'Sarah Thompson', date(2026,6,15), 'open', 'high'),
            ('Confirm subcontractor pre-qualification for all trades', 'James Kowalski', date(2026,6,30), 'open', 'medium'),
        ])
        self._seed_risks(tenant, p2, [
            ('DA conditions require community consultation — potential objections', 2, 4, 'Engage community liaison consultant'),
            ('Site access limited to single lane — delivery logistics risk', 3, 2, 'Coordinate delivery schedule with council'),
        ])

        return tenant

    def _seed_tenant2(self):
        """Tenant 2: Hinterland Heritage Homes"""
        tenant, _ = Tenant.objects.get_or_create(
            name='Hinterland Heritage Homes',
            defaults={'org_id': 'org_hhh_demo_002', 'is_active': True}
        )
        self.stdout.write(f'  ✓ Tenant: {tenant.name}')

        TenantUser.objects.get_or_create(
            tenant=tenant, email='director@heritagehomes.com.au',
            defaults={'name': 'Patricia Nguyen', 'role': 'admin', 'mfa_enabled': True}
        )
        TenantUser.objects.get_or_create(
            tenant=tenant, email='pm@heritagehomes.com.au',
            defaults={'name': 'Tom Birch', 'role': 'pm', 'mfa_enabled': True}
        )

        for name, cat, rating in [
            ('Blackall Range Timber', 'Timber', 9), ('Mapleton Masonry', 'Masonry', 8),
        ]:
            Vendor.objects.get_or_create(tenant=tenant, name=name, defaults={'category': cat, 'rating': rating})

        project, _ = Project.objects.get_or_create(
            tenant=tenant, name='Maleny Heritage Restoration',
            defaults={'client': 'Heritage Council QLD', 'status': 'active',
                      'start_date': date(2025, 11, 1), 'end_date': date(2026, 10, 31),
                      'health_score': 61, 'description': 'Heritage-listed 1920s farmhouse full restoration'}
        )
        self._seed_phases(tenant, project, [
            (1, 'Structural Assessment & Stabilisation', 'complete', 100, date(2025,11,1), date(2026,1,31)),
            (2, 'Roof Restoration — Original Shingles', 'in_progress', 55, date(2026,2,1), date(2026,5,31)),
            (3, 'External Walls & Verandah', 'not_started', 0, date(2026,5,1), date(2026,8,31)),
            (4, 'Internal Restoration', 'not_started', 0, date(2026,7,1), date(2026,10,31)),
        ])
        self._seed_actions(tenant, project, [
            ('Source matching heritage shingles — supplier quote needed', 'Tom Birch', date(2026,5,10), 'overdue', 'critical'),
            ('Submit heritage council interim progress report', 'Patricia Nguyen', date(2026,5,31), 'open', 'high'),
            ('Confirm lime mortar specification with heritage architect', 'Tom Birch', date(2026,6,15), 'open', 'medium'),
        ])
        self._seed_risks(tenant, project, [
            ('Heritage shingles discontinued — may require custom fabrication', 5, 4, 'Contact 3 specialist suppliers immediately; escalate to heritage council'),
            ('Subfloor rot found in section B — scope expansion risk', 3, 4, 'Structural engineer assessment in progress'),
            ('Weather damage to exposed roof section during restoration', 4, 3, 'Temporary waterproof cover installed'),
        ])
        self._seed_budget(tenant, project, [
            (None, 'Structural Assessment', 35000, 36200),
            (None, 'Roof Restoration', 120000, 68000),
            (None, 'External Works', 85000, 0),
            (None, 'Internal Restoration', 145000, 0),
            (None, 'Heritage Compliance', 25000, 12800),
            (None, 'Contingency', 41000, 8200),
        ])
        return tenant

    def _seed_phases(self, tenant, project, phases_data):
        for order, name, status, pct, start, end in phases_data:
            Phase.objects.get_or_create(
                tenant=tenant, project=project, name=name,
                defaults={'status': status, 'completion_pct': pct,
                          'start_date': start, 'end_date': end, 'order': order}
            )

    def _seed_actions(self, tenant, project, actions_data):
        for title, owner, due, status, priority in actions_data:
            ActionItem.objects.get_or_create(
                tenant=tenant, project=project, title=title,
                defaults={'owner': owner, 'due_date': due, 'status': status, 'priority': priority}
            )

    def _seed_risks(self, tenant, project, risks_data):
        for desc, likelihood, impact, mitigation in risks_data:
            Risk.objects.get_or_create(
                tenant=tenant, project=project, description=desc,
                defaults={'likelihood': likelihood, 'impact': impact, 'mitigation': mitigation, 'status': 'open'}
            )

    def _seed_budget(self, tenant, project, budget_data):
        for phase, desc, estimated, actual in budget_data:
            Budget.objects.get_or_create(
                tenant=tenant, project=project, description=desc,
                defaults={'estimated': estimated, 'actual': actual}
            )
