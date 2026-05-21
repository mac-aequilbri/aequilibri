"""Migration: Add Vendor, VendorMaterialPrice, PurchaseOrder, PurchaseOrderItem"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('uc1_roofing', '0003_alter_quote_flat_area_sqm_alter_quote_material_and_more'),
    ]

    operations = [
        # ── Vendor ────────────────────────────────────────────────────────────
        migrations.CreateModel(
            name='Vendor',
            fields=[
                ('id',            models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name',          models.CharField(max_length=200)),
                ('contact_name',  models.CharField(blank=True, max_length=200)),
                ('contact_email', models.EmailField(blank=True)),
                ('contact_phone', models.CharField(blank=True, max_length=30)),
                ('website',       models.URLField(blank=True)),
                ('suburb',        models.CharField(blank=True, max_length=100)),
                ('state',         models.CharField(blank=True, default='QLD', max_length=10)),
                ('notes',         models.TextField(blank=True)),
                ('is_preferred',  models.BooleanField(default=False, help_text='Highlight as preferred vendor')),
                ('is_active',     models.BooleanField(default=True)),
                ('created_at',    models.DateTimeField(auto_now_add=True)),
            ],
            options={'ordering': ['-is_preferred', 'name'], 'verbose_name': 'Vendor'},
        ),

        # ── VendorMaterialPrice ───────────────────────────────────────────────
        migrations.CreateModel(
            name='VendorMaterialPrice',
            fields=[
                ('id',                  models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('vendor',              models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='prices', to='uc1_roofing.vendor')),
                ('material',            models.CharField(max_length=50, choices=[('colorbond','Colorbond Steel'),('terracotta','Terracotta Tiles'),('concrete','Concrete Tiles'),('zincalume','Zincalume'),('slate','Natural Slate'),('asphalt','Asphalt Shingles')])),
                ('item_code',           models.CharField(blank=True, help_text='Vendor SKU / product code', max_length=50)),
                ('description',         models.CharField(max_length=300)),
                ('unit',                models.CharField(default='m²', max_length=20)),
                ('unit_price_ex_gst',   models.DecimalField(decimal_places=2, max_digits=10)),
                ('lead_days',           models.PositiveSmallIntegerField(default=3, help_text='Typical lead time in business days')),
                ('is_available',        models.BooleanField(default=True)),
                ('updated_at',          models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['material', 'unit_price_ex_gst'], 'verbose_name': 'Vendor Material Price'},
        ),
        migrations.AddConstraint(
            model_name='vendormaterialprice',
            constraint=models.UniqueConstraint(fields=['vendor', 'material'], name='unique_vendor_material'),
        ),

        # ── PurchaseOrder ─────────────────────────────────────────────────────
        migrations.CreateModel(
            name='PurchaseOrder',
            fields=[
                ('id',                       models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('po_number',                models.CharField(editable=False, max_length=20, unique=True)),
                ('quote',                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='purchase_orders', to='uc1_roofing.quote')),
                ('vendor',                   models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='purchase_orders', to='uc1_roofing.vendor')),
                ('status',                   models.CharField(choices=[('draft','Draft'),('sent','Sent to Vendor'),('confirmed','Confirmed'),('cancelled','Cancelled')], default='draft', max_length=20)),
                ('delivery_address',         models.TextField(blank=True, help_text='Defaults to property address from quote')),
                ('requested_delivery_date',  models.DateField(blank=True, null=True)),
                ('notes',                    models.TextField(blank=True)),
                ('created_at',               models.DateTimeField(auto_now_add=True)),
                ('updated_at',               models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['-created_at'], 'verbose_name': 'Purchase Order'},
        ),

        # ── PurchaseOrderItem ─────────────────────────────────────────────────
        migrations.CreateModel(
            name='PurchaseOrderItem',
            fields=[
                ('id',                  models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('purchase_order',      models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='po_items', to='uc1_roofing.purchaseorder')),
                ('description',         models.CharField(max_length=300)),
                ('item_code',           models.CharField(blank=True, max_length=50)),
                ('quantity',            models.DecimalField(decimal_places=2, max_digits=10)),
                ('unit',                models.CharField(default='m²', max_length=20)),
                ('unit_price_ex_gst',   models.DecimalField(decimal_places=2, max_digits=10)),
                ('sort_order',          models.PositiveSmallIntegerField(default=0)),
            ],
            options={'ordering': ['sort_order', 'id'], 'verbose_name': 'Purchase Order Item'},
        ),
    ]
