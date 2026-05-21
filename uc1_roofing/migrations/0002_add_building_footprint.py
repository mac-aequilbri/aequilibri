"""
0002_add_building_footprint
Adds the BuildingFootprint table that stores Microsoft ML-derived
building polygons for QLD, used for accurate roof area estimation.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('uc1_roofing', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='BuildingFootprint',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('min_lat',      models.FloatField()),
                ('max_lat',      models.FloatField()),
                ('min_lon',      models.FloatField()),
                ('max_lon',      models.FloatField()),
                ('centroid_lat', models.FloatField()),
                ('centroid_lon', models.FloatField()),
                ('area_sqm',     models.FloatField()),
                # Outer-ring coordinates: [[lon, lat], ...]  (GeoJSON order)
                ('geometry',     models.TextField()),
            ],
            options={
                'verbose_name': 'Building Footprint (ML)',
                'indexes': [],
            },
        ),
        migrations.AddIndex(
            model_name='buildingfootprint',
            index=models.Index(fields=['min_lat', 'max_lat'], name='bf_lat_idx'),
        ),
        migrations.AddIndex(
            model_name='buildingfootprint',
            index=models.Index(fields=['min_lon', 'max_lon'], name='bf_lon_idx'),
        ),
    ]
