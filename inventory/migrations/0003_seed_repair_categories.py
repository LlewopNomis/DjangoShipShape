from django.db import migrations

DEFAULT_CATEGORIES = [
    (
        'Routine maintenance',
        'Scheduled, interval-based servicing — oil, coolant, filters, and the like.',
    ),
    (
        'Condition-based repair',
        'Found during inspection and fixed before it fails — not on a schedule, '
        'not an emergency (e.g. replacing corroded engine mounts).',
    ),
    (
        'Emergency repair',
        'Unplanned failure, fixed underway or urgently.',
    ),
    (
        'Upgrade / improvement',
        'Not a repair — discretionary improvements or additions.',
    ),
]


def seed_categories(apps, schema_editor):
    RepairCategory = apps.get_model('inventory', 'RepairCategory')
    for name, description in DEFAULT_CATEGORIES:
        RepairCategory.objects.get_or_create(name=name, defaults={'description': description})


def remove_seeded_categories(apps, schema_editor):
    RepairCategory = apps.get_model('inventory', 'RepairCategory')
    RepairCategory.objects.filter(name__in=[name for name, _ in DEFAULT_CATEGORIES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0002_repaircategory_inventoryitem_value_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_categories, remove_seeded_categories),
    ]
