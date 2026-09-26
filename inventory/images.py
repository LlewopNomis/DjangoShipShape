"""Downsizing of uploaded photos, so phone-camera shots (3-5 MB, often
stored sideways with an EXIF rotation flag) are stored upright and at a
sensible size."""

import io

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageOps

# Formats worth re-encoding, and how. GIFs are left alone (could be
# animated), and PDFs aren't images at all. MPO is the multi-picture JPEG
# many phone cameras save (a JPEG with extra depth/preview frames tacked
# on) — it's stored as a plain JPEG of the main picture.
_SAVE_AS = {
    'JPEG': ('JPEG', {'quality': 85, 'optimize': True}),
    'MPO': ('JPEG', {'quality': 85, 'optimize': True}),
    'PNG': ('PNG', {'optimize': True}),
    'WEBP': ('WEBP', {'quality': 85}),
}
_EXIF_ORIENTATION = 0x0112


def shrunk_image_bytes(fileobj, max_dim):
    """Return the image resized to fit max_dim and rotated upright, as
    bytes in a format matching its file extension — or None if it's already
    fine, isn't a format worth touching, or Pillow can't read it."""
    try:
        with Image.open(fileobj) as img:
            if img.format not in _SAVE_AS:
                return None
            rotated = img.getexif().get(_EXIF_ORIENTATION, 1) != 1
            if not rotated and max(img.size) <= max_dim and img.format != 'MPO':
                return None
            fmt, options = _SAVE_AS[img.format]
            img = ImageOps.exif_transpose(img)
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            if fmt == 'JPEG' and img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            out = io.BytesIO()
            # Re-encoding drops the rest of the EXIF data too, including any
            # GPS position the phone embedded.
            img.save(out, format=fmt, **options)
            return out.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def shrink_photo(field_file):
    """Resize a just-uploaded image in place to fit PHOTO_MAX_DIMENSION, and
    bake in its EXIF rotation. Call before the model is saved. Files already
    on disk, non-images, and anything Pillow can't read are left untouched —
    an odd photo should still upload, just at full size."""
    if not field_file or getattr(field_file, '_committed', True):
        return
    field_file.seek(0)
    data = shrunk_image_bytes(field_file, settings.PHOTO_MAX_DIMENSION)
    field_file.seek(0)
    if data is not None:
        field_file.file = ContentFile(data, name=field_file.name)
