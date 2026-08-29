from django.db import migrations

DEFAULT_UNITS = ['mm', 'cm', 'm', 'ml', 'L', 'g', 'kg']


def seed_units(apps, schema_editor):
    Unit = apps.get_model('inventory', 'Unit')
    for name in DEFAULT_UNITS:
        Unit.objects.get_or_create(name=name)


def remove_seeded_units(apps, schema_editor):
    Unit = apps.get_model('inventory', 'Unit')
    Unit.objects.filter(name__in=DEFAULT_UNITS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0009_rename_value_inventoryitem_unit_price_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_units, remove_seeded_units),
    ]
