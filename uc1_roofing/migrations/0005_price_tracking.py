"""Migration: Add price tracking fields to VendorMaterialPrice + PriceCheckLog model"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('uc1_roofing', '0004_vendor_purchaseorder'),
    ]

    operations = [
        # New fields on VendorMaterialPrice
        migrations.AddField(
            model_name='vendormaterialprice',
            name='price_source_url',
            field=models.URLField(blank=True, help_text='Vendor product page used for price verification'),
        ),
        migrations.AddField(
            model_name='vendormaterialprice',
            name='previous_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True,
                                       help_text='Price before last update (for change tracking)'),
        ),
        migrations.AddField(
            model_name='vendormaterialprice',
            name='last_verified',
            field=models.DateTimeField(blank=True, null=True,
                                        help_text='When this price was last confirmed against vendor source'),
        ),

        # PriceCheckLog
        migrations.CreateModel(
            name='PriceCheckLog',
            fields=[
                ('id',               models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('run_at',           models.DateTimeField(auto_now_add=True)),
                ('status',           models.CharField(max_length=20,
                                                       choices=[('success','Success'),('partial','Partial — some prices updated'),('no_change','No Change'),('error','Error')],
                                                       default='success')),
                ('vendors_checked',  models.PositiveSmallIntegerField(default=0)),
                ('prices_updated',   models.PositiveSmallIntegerField(default=0)),
                ('prices_unchanged', models.PositiveSmallIntegerField(default=0)),
                ('errors',           models.PositiveSmallIntegerField(default=0)),
                ('summary',          models.TextField(blank=True)),
                ('raw_log',          models.TextField(blank=True)),
            ],
            options={'ordering': ['-run_at'], 'verbose_name': 'Price Check Log'},
        ),
    ]
