"""Apply upload-time photo resizing to photos already on disk — for
anything uploaded before inventory.images.shrink_photo existed."""

import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from inventory.images import shrunk_image_bytes
from inventory.models import ItemPhoto, LocationPhoto, RepairPhoto, SparePhoto

PHOTO_MODELS = [ItemPhoto, SparePhoto, LocationPhoto, RepairPhoto]


class Command(BaseCommand):
    help = (
        'Resize existing photos to fit PHOTO_MAX_DIMENSION and rotate them upright, '
        'overwriting each file in place (file names and database rows are unchanged). '
        'Back up media/ first.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report what would change without writing.')

    def handle(self, *args, dry_run=False, **options):
        max_dim = settings.PHOTO_MAX_DIMENSION
        shrunk = skipped = missing = 0
        before = after = 0
        for model in PHOTO_MODELS:
            for photo in model.objects.exclude(image=''):
                path = Path(photo.image.path)
                if not path.exists():
                    missing += 1
                    self.stderr.write(f'Missing file: {path}')
                    continue
                with path.open('rb') as f:
                    data = shrunk_image_bytes(f, max_dim)
                if data is None:
                    skipped += 1
                    continue
                shrunk += 1
                before += path.stat().st_size
                after += len(data)
                if not dry_run:
                    # Write alongside and rename over, so an interruption
                    # never leaves a half-written photo behind.
                    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
                    with os.fdopen(fd, 'wb') as out:
                        out.write(data)
                    os.chmod(tmp, path.stat().st_mode)
                    os.replace(tmp, path)

        verb = 'would be shrunk' if dry_run else 'shrunk'
        mb = 1024 * 1024
        self.stdout.write(
            f'{shrunk} {verb}, {skipped} already fine or not a photo, {missing} missing. '
            f'{before / mb:.1f} MB -> {after / mb:.1f} MB.'
        )
