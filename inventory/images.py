"""Downsizing of uploaded photos, so phone-camera shots (3-5 MB, often
stored sideways with an EXIF rotation flag) are stored upright and at a
sensible size."""

import io

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageOps

# Formats worth re-encoding. GIFs are left alone (could be animated), and
# PDFs aren't images at all.
_SHRINKABLE = {'JPEG': {'quality': 85, 'optimize': True}, 'PNG': {'optimize': True}, 'WEBP': {'quality': 85}}
_EXIF_ORIENTATION = 0x0112


def shrink_photo(field_file):
    """Resize a just-uploaded image in place to fit PHOTO_MAX_DIMENSION, and
    bake in its EXIF rotation. Call before the model is saved. Files already
    on disk, non-images, and anything Pillow can't read are left untouched —
    an odd photo should still upload, just at full size."""
    if not field_file or getattr(field_file, '_committed', True):
        return
    max_dim = settings.PHOTO_MAX_DIMENSION
    try:
        field_file.seek(0)
        with Image.open(field_file) as img:
            fmt = img.format
            if fmt not in _SHRINKABLE:
                return
            rotated = img.getexif().get(_EXIF_ORIENTATION, 1) != 1
            if not rotated and max(img.size) <= max_dim:
                return
            img = ImageOps.exif_transpose(img)
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            if fmt == 'JPEG' and img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            out = io.BytesIO()
            # Re-encoding drops the rest of the EXIF data too, including any
            # GPS position the phone embedded.
            img.save(out, format=fmt, **_SHRINKABLE[fmt])
    except (OSError, ValueError, Image.DecompressionBombError):
        field_file.seek(0)
        return
    field_file.file = ContentFile(out.getvalue(), name=field_file.name)
