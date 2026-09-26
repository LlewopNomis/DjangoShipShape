import io
import os
import shutil
import subprocess
import sys
import tempfile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import InventoryItem, ItemPhoto, Location

TEMP_MEDIA_ROOT = tempfile.mkdtemp()


def make_mpo(size, orientation=None):
    """A phone-style multi-picture JPEG: the main shot plus a small extra frame."""
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    out = io.BytesIO()
    Image.new('RGB', size, 'navy').save(
        out, format='MPO', save_all=True, append_images=[Image.new('RGB', (100, 75))], exif=exif,
    )
    return out.getvalue()


def make_jpeg(size, orientation=None):
    img = Image.new('RGB', size, 'navy')
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    out = io.BytesIO()
    img.save(out, format='JPEG', exif=exif)
    return out.getvalue()


class LoginRequiredTests(TestCase):
    def test_pages_redirect_to_login(self):
        response = self.client.get(reverse('inventory:item_list'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('inventory:item_list')}")

    def test_media_needs_login_too(self):
        response = self.client.get('/media/items/1/anything.jpg')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith(reverse('login')))

    def test_login_page_renders(self):
        self.assertEqual(self.client.get(reverse('login')).status_code, 200)

    def test_logged_in_user_sees_pages(self):
        user = get_user_model().objects.create_user('simon', password='pw')
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('inventory:item_list')).status_code, 200)


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT, PHOTO_MAX_DIMENSION=1600)
class ShrinkPhotoTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.item = InventoryItem.objects.create(name='Impeller', location=Location.add_root(name='Engine bay'))

    def upload(self, name, data):
        photo = ItemPhoto(item=self.item, image=SimpleUploadedFile(name, data))
        photo.save()
        photo.image.open('rb')
        try:
            return photo.image.read()
        finally:
            photo.image.close()

    def test_large_photo_is_downsized(self):
        stored = self.upload('big.jpg', make_jpeg((4000, 3000)))
        self.assertEqual(Image.open(io.BytesIO(stored)).size, (1600, 1200))

    def test_sideways_phone_photo_is_stored_upright(self):
        # Orientation 6 = "rotate 90° clockwise to display", as phones save portrait shots.
        stored = self.upload('portrait.jpg', make_jpeg((4000, 3000), orientation=6))
        img = Image.open(io.BytesIO(stored))
        self.assertEqual(img.size, (1200, 1600))
        self.assertEqual(img.getexif().get(0x0112, 1), 1)

    def test_phone_mpo_photo_is_downsized_to_plain_jpeg(self):
        stored = self.upload('phone.jpg', make_mpo((4000, 3000), orientation=6))
        img = Image.open(io.BytesIO(stored))
        self.assertEqual((img.format, img.size), ('JPEG', (1200, 1600)))

    def test_small_upright_photo_is_left_untouched(self):
        original = make_jpeg((800, 600))
        self.assertEqual(self.upload('small.jpg', original), original)

    def test_pdf_and_unreadable_files_are_left_untouched(self):
        pdf = b'%PDF-1.4 not really a pdf'
        self.assertEqual(self.upload('manual.pdf', pdf), pdf)
        junk = b'not a jpeg at all'
        self.assertEqual(self.upload('broken.jpg', junk), junk)

    def test_existing_photo_is_not_reprocessed_on_resave(self):
        self.upload('big.jpg', make_jpeg((4000, 3000)))
        photo = ItemPhoto.objects.get()
        name = photo.image.name
        photo.caption = 'Old impeller'
        photo.save()
        self.assertEqual(ItemPhoto.objects.get().image.name, name)


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT, PHOTO_MAX_DIMENSION=1600)
class ShrinkPhotosCommandTests(TestCase):
    def test_shrinks_existing_photos_in_place(self):
        item = InventoryItem.objects.create(name='Impeller', location=Location.add_root(name='Engine bay'))
        # Written straight to storage, bypassing save()'s shrinking, as older uploads were.
        photo = ItemPhoto(item=item)
        photo.image.save('old.jpg', ContentFile(make_mpo((4000, 3000))), save=False)
        ItemPhoto.objects.bulk_create([photo])
        path = ItemPhoto.objects.get().image.path

        call_command('shrink_photos', '--dry-run', stdout=io.StringIO())
        self.assertEqual(Image.open(path).size, (4000, 3000))

        out = io.StringIO()
        call_command('shrink_photos', stdout=out)
        self.assertEqual(Image.open(path).size, (1600, 1200))
        self.assertIn('1 shrunk', out.getvalue())

        out = io.StringIO()
        call_command('shrink_photos', stdout=out)
        self.assertIn('0 shrunk', out.getvalue())


class ProductionSettingsTests(TestCase):
    def import_settings(self, **env):
        base = {k: v for k, v in os.environ.items() if not k.startswith('DJANGO_')}
        return subprocess.run(
            [sys.executable, '-c', 'import django; django.setup()'],
            env={**base, 'DJANGO_SETTINGS_MODULE': 'djangoshipshape.settings', **env},
            cwd=settings.BASE_DIR, capture_output=True, text=True,
        )

    def test_debug_off_refuses_the_dev_secret_key(self):
        result = self.import_settings(DJANGO_DEBUG='0')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('DJANGO_SECRET_KEY', result.stderr)

    def test_debug_off_with_a_real_key_starts(self):
        result = self.import_settings(DJANGO_DEBUG='0', DJANGO_SECRET_KEY='x' * 50, DJANGO_ALLOWED_HOSTS='ionos-vps')
        self.assertEqual(result.returncode, 0, result.stderr)
