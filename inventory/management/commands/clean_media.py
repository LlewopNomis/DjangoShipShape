"""List (or delete) files in MEDIA_ROOT that no database row points at —
left over from deletes made before uploaded files were cleaned up
automatically (see FILE_FIELDS in inventory.models), or copied in by hand."""

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from inventory.models import FILE_FIELDS


class Command(BaseCommand):
    help = 'List files in media/ that nothing in the database uses. Add --delete to remove them.'

    def add_arguments(self, parser):
        parser.add_argument('--delete', action='store_true', help='Delete the orphaned files (default: just list them).')

    def handle(self, *args, delete=False, **options):
        root = Path(settings.MEDIA_ROOT)
        used = {
            name
            for model, field in FILE_FIELDS.items()
            for name in model.objects.exclude(**{field: ''}).values_list(field, flat=True)
        }
        orphans = sorted(
            p for p in root.rglob('*')
            if p.is_file() and p.relative_to(root).as_posix() not in used
        )
        size_mb = sum(p.stat().st_size for p in orphans) / (1024 * 1024)
        for path in orphans:
            self.stdout.write(f'  {path.relative_to(root)}  ({path.stat().st_size / 1024:.0f} KB)')
            if delete:
                path.unlink()
                if path.parent != root and not any(path.parent.iterdir()):
                    os.rmdir(path.parent)

        if delete:
            self.stdout.write(f'Deleted {len(orphans)} orphaned file(s), {size_mb:.1f} MB.')
        else:
            self.stdout.write(f'{len(orphans)} orphaned file(s), {size_mb:.1f} MB. Re-run with --delete to remove them.')
