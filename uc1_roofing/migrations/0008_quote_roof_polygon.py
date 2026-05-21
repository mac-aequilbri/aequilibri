from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('uc1_roofing', '0007_merge_20260514_1851'),
    ]

    operations = [
        migrations.AddField(
            model_name='quote',
            name='roof_polygon_json',
            field=models.TextField(
                blank=True, default='',
                help_text='JSON [[lat,lon],...] building footprint from MS footprints',
            ),
        ),
        migrations.AddField(
            model_name='quote',
            name='roof_sections_json',
            field=models.TextField(
                blank=True, default='',
                help_text='JSON Solar API section list (facing, area, bbox, pitch)',
            ),
        ),
    ]
