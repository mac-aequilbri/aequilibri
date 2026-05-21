"""Seed UC1 Roofing Estimator with realistic initial data."""
from django.core.management.base import BaseCommand
from uc1_roofing.models import RateCard, Contact, Quote, QuoteItem, PITCH_FACTORS


RATE_CARD_DATA = [
    # Colorbond
    ('colorbond', 'flat',       'Colorbond Steel — Flat Roof supply & install',           'm²', 42.00),
    ('colorbond', 'low',        'Colorbond Steel — Low Pitch supply & install',            'm²', 45.00),
    ('colorbond', 'standard',   'Colorbond Steel — Standard Pitch supply & install',       'm²', 48.00),
    ('colorbond', 'steep',      'Colorbond Steel — Steep Pitch supply & install',          'm²', 55.00),
    ('colorbond', 'very_steep', 'Colorbond Steel — Very Steep Pitch supply & install',     'm²', 68.00),
    # Terracotta
    ('terracotta', 'low',       'Terracotta Tiles — Low Pitch supply & install',           'm²', 62.00),
    ('terracotta', 'standard',  'Terracotta Tiles — Standard Pitch supply & install',      'm²', 68.00),
    ('terracotta', 'steep',     'Terracotta Tiles — Steep Pitch supply & install',         'm²', 78.00),
    ('terracotta', 'very_steep','Terracotta Tiles — Very Steep Pitch supply & install',    'm²', 92.00),
    # Concrete
    ('concrete', 'low',         'Concrete Tiles — Low Pitch supply & install',             'm²', 54.00),
    ('concrete', 'standard',    'Concrete Tiles — Standard Pitch supply & install',        'm²', 60.00),
    ('concrete', 'steep',       'Concrete Tiles — Steep Pitch supply & install',           'm²', 70.00),
    # Zincalume
    ('zincalume', 'flat',       'Zincalume — Flat Roof supply & install',                  'm²', 38.00),
    ('zincalume', 'standard',   'Zincalume — Standard Pitch supply & install',             'm²', 44.00),
    # Slate
    ('slate', 'standard',       'Natural Slate — Standard Pitch supply & install',         'm²', 145.00),
    ('slate', 'steep',          'Natural Slate — Steep Pitch supply & install',            'm²', 165.00),
    # Asphalt
    ('asphalt', 'low',          'Asphalt Shingles — Low Pitch supply & install',           'm²', 36.00),
    ('asphalt', 'standard',     'Asphalt Shingles — Standard Pitch supply & install',      'm²', 40.00),
]

CONTACT_DATA = [
    ('Jack Henderson', 'Aboda Investments Pty Ltd', 'jack@aboda.com.au', '0412 345 678', '199 Dulong Road, Dulong QLD 4560'),
    ('Claudia Salem', 'Salem Properties', 'claudia@salemprop.com.au', '0423 456 789', '15 Maple Street, Noosa Heads QLD 4567'),
    ('Paul Sakrzewski', 'P&P Building Group', 'paul@ppbuilding.com.au', '0434 567 890', '88 Cooroy Road, Tewantin QLD 4565'),
    ('Sophie Martin', 'Martin Constructions', 'sophie@martinconstruct.com.au', '0445 678 901', '42 Pacific Drive, Coolum Beach QLD 4573'),
    ('Ray Dawson', 'Dawson Roofing', 'ray@dawsonroofing.com.au', '0456 789 012', '7 Industrial Ave, Maroochydore QLD 4558'),
]

QUOTE_DATA = [
    {
        'contact_idx': 0,
        'address': '199 Dulong Road, Dulong QLD 4560',
        'flat_area': 245.0, 'pitch': 'standard', 'waste': 12.0, 'material': 'colorbond',
        'status': 'accepted',
        'notes': 'Main residence — full re-roof. Existing Zincalume to be removed.',
    },
    {
        'contact_idx': 1,
        'address': '15 Maple Street, Noosa Heads QLD 4567',
        'flat_area': 180.5, 'pitch': 'steep', 'waste': 10.0, 'material': 'terracotta',
        'status': 'sent',
        'notes': 'Heritage character dwelling — matching terracotta colour required.',
    },
    {
        'contact_idx': 2,
        'address': '88 Cooroy Road, Tewantin QLD 4565',
        'flat_area': 320.0, 'pitch': 'low', 'waste': 10.0, 'material': 'colorbond',
        'status': 'draft',
        'notes': 'New construction — frame and truss complete.',
    },
    {
        'contact_idx': 3,
        'address': '42 Pacific Drive, Coolum Beach QLD 4573',
        'flat_area': 95.0, 'pitch': 'very_steep', 'waste': 15.0, 'material': 'slate',
        'status': 'draft',
        'notes': 'Extension only — match existing slate profile.',
    },
]


class Command(BaseCommand):
    help = 'Seed UC1 Roofing Estimator with initial rate cards, contacts, and sample quotes'

    def handle(self, *args, **options):
        self.stdout.write('Seeding UC1 Roofing Estimator...')

        # Rate Cards
        created_rc = 0
        for material, pitch, desc, unit, rate in RATE_CARD_DATA:
            _, created = RateCard.objects.get_or_create(
                material=material, pitch_type=pitch,
                defaults={'description': desc, 'unit': unit, 'rate_ex_gst': rate, 'is_active': True}
            )
            if created:
                created_rc += 1
        self.stdout.write(f'  ✓ {created_rc} rate cards created')

        # Contacts
        contacts = []
        for name, company, email, phone, address in CONTACT_DATA:
            c, _ = Contact.objects.get_or_create(
                name=name,
                defaults={'company': company, 'email': email, 'phone': phone, 'address': address}
            )
            contacts.append(c)
        self.stdout.write(f'  ✓ {len(contacts)} contacts created')

        # Quotes
        created_q = 0
        for qd in QUOTE_DATA:
            contact = contacts[qd['contact_idx']]
            if Quote.objects.filter(property_address=qd['address']).exists():
                continue
            q = Quote.objects.create(
                contact=contact,
                property_address=qd['address'],
                flat_area_sqm=qd['flat_area'],
                pitch_type=qd['pitch'],
                waste_factor_pct=qd['waste'],
                material=qd['material'],
                status=qd['status'],
                notes=qd['notes'],
            )
            # Add main line item from rate card
            try:
                rc = RateCard.objects.get(material=q.material, pitch_type=q.pitch_type)
                QuoteItem.objects.create(
                    quote=q, description=rc.description,
                    quantity=q.adjusted_area_sqm, unit='m²',
                    unit_price_ex_gst=rc.rate_ex_gst, sort_order=1,
                )
            except RateCard.DoesNotExist:
                pass
            # Scaffold
            QuoteItem.objects.create(
                quote=q, description='Scaffolding & Safety — site access package',
                quantity=1, unit='lot', unit_price_ex_gst=1200.00, sort_order=2,
            )
            # Demo
            QuoteItem.objects.create(
                quote=q, description='Removal & disposal of existing roofing material',
                quantity=float(q.adjusted_area_sqm), unit='m²',
                unit_price_ex_gst=8.50, sort_order=3,
            )
            created_q += 1
        self.stdout.write(f'  ✓ {created_q} sample quotes created')
        self.stdout.write(self.style.SUCCESS('UC1 seeding complete!'))
