from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('uc1_roofing', '0005_price_tracking'),
    ]

    operations = [
        migrations.CreateModel(
            name='RoofLidarAnalysis',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('perimeter_m',          models.FloatField(default=0, help_text='Building perimeter in metres')),
                ('guttering_linear_m',   models.FloatField(default=0, help_text='Estimated guttering linear metres')),
                ('ridge_height_m',       models.FloatField(blank=True, null=True, help_text='Roof ridge height above ground (m)')),
                ('eave_height_m',        models.FloatField(blank=True, null=True, help_text='Eave height above ground (m)')),
                ('height_range_m',       models.FloatField(blank=True, null=True, help_text='Vertical height of roof (ridge − eave)')),
                ('scaffolding_required',     models.BooleanField(default=False)),
                ('scaffolding_linear_m',     models.FloatField(default=0, help_text='Estimated scaffolding linear metres')),
                ('scaffolding_risk_level',   models.CharField(choices=[('low', 'Low'), ('medium', 'Medium'), ('high', 'High')], default='low', max_length=10)),
                ('scaffolding_reason',       models.CharField(blank=True, max_length=200)),
                ('structure_count',      models.PositiveSmallIntegerField(default=1, help_text='Number of structures detected on lot')),
                ('structures_json',      models.TextField(default='[]', help_text='JSON list of all detected structures')),
                ('solar_panels',         models.BooleanField(default=False)),
                ('solar_hw',             models.BooleanField(default=False, help_text='Solar hot water system present')),
                ('lidar_coverage',       models.CharField(choices=[('full', 'Full — 1m LiDAR DSM + DTM'), ('partial', 'Partial — DSM only'), ('estimated', 'Estimated — storey-based fallback'), ('none', 'None — no data available')], default='none', max_length=20)),
                ('data_source',          models.CharField(blank=True, max_length=50)),
                ('analysis_notes',       models.TextField(blank=True, help_text='JSON list of analysis notes')),
                ('elapsed_ms',           models.IntegerField(default=0)),
                ('analyzed_at',          models.DateTimeField(auto_now=True)),
                ('quote',                models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='lidar_analysis', to='uc1_roofing.quote')),
            ],
            options={'verbose_name': 'Roof LiDAR Analysis'},
        ),
    ]
