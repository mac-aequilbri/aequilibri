"""
0001_initial — represents all tables that were created by syncdb.
Run with: python manage.py migrate uc1_roofing --fake-initial
Django will skip creating tables that already exist.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Contact',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('phone', models.CharField(blank=True, max_length=30)),
                ('company', models.CharField(blank=True, max_length=200)),
                ('address', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'verbose_name': 'Contact', 'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='RateCard',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('material', models.CharField(max_length=50)),
                ('pitch_type', models.CharField(max_length=20)),
                ('description', models.CharField(max_length=300)),
                ('unit', models.CharField(default='m²', max_length=20)),
                ('rate_ex_gst', models.DecimalField(decimal_places=2, max_digits=10)),
                ('is_active', models.BooleanField(default=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'verbose_name': 'Rate Card', 'ordering': ['material', 'pitch_type']},
        ),
        migrations.CreateModel(
            name='Quote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ref_number', models.CharField(editable=False, max_length=20, unique=True)),
                ('contact', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                               related_name='quotes', to='uc1_roofing.contact')),
                ('property_address', models.TextField()),
                ('flat_area_sqm', models.DecimalField(decimal_places=2, max_digits=10)),
                ('pitch_type', models.CharField(default='standard', max_length=20)),
                ('waste_factor_pct', models.DecimalField(decimal_places=1, default=10.0, max_digits=5)),
                ('material', models.CharField(default='colorbond', max_length=50)),
                ('notes', models.TextField(blank=True)),
                ('status', models.CharField(default='draft', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'verbose_name': 'Quote', 'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='QuoteItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quote', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                            related_name='items', to='uc1_roofing.quote')),
                ('description', models.CharField(max_length=300)),
                ('quantity', models.DecimalField(decimal_places=2, max_digits=10)),
                ('unit', models.CharField(default='m²', max_length=20)),
                ('unit_price_ex_gst', models.DecimalField(decimal_places=2, max_digits=10)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
            ],
            options={'verbose_name': 'Quote Item', 'ordering': ['sort_order', 'id']},
        ),
        migrations.CreateModel(
            name='RoofPolygon',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quote', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                               related_name='polygon', to='uc1_roofing.quote')),
                ('coordinates_json', models.TextField(default='[]')),
                ('detection_path', models.CharField(blank=True, max_length=50)),
                ('confidence', models.CharField(blank=True, max_length=20)),
                ('area_sqm_raw', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'verbose_name': 'Roof Polygon'},
        ),
        migrations.CreateModel(
            name='ExecutionLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tool_name', models.CharField(max_length=100)),
                ('payload', models.TextField(default='{}')),
                ('result', models.TextField(default='{}')),
                ('status', models.CharField(default='success', max_length=20)),
                ('duration_ms', models.IntegerField(default=0)),
                ('quote', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                            related_name='execution_logs', to='uc1_roofing.quote')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'verbose_name': 'Execution Log (UC1)', 'ordering': ['-created_at']},
        ),
        migrations.AlterUniqueTogether(
            name='ratecard',
            unique_together={('material', 'pitch_type')},
        ),
    ]
