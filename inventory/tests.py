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

from .forms import PartRequirementForm
from .models import (
    CatalogPart, CatalogSection, CatalogSource, InventoryItem, ItemPhoto, Job, Location, LocationPhoto,
    PartRequirement, Repair, RepairPhoto, Spare, SparePhoto,
)
from .templatetags.inventory_extras import catalog_part_field

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


class DeletedFilesAreRemovedTests(TestCase):
    """Deleting a row with an uploaded file — directly, or by cascade from
    the thing it's attached to — removes the file (and its emptied folder)."""

    def setUp(self):
        # A fresh media folder per test: row ids repeat across tests, so a
        # shared one would leave other tests' files in e.g. items/1/.
        media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media_root, ignore_errors=True)
        self.enterContext(override_settings(MEDIA_ROOT=media_root))
        self.client.force_login(get_user_model().objects.create_user('simon', password='pw'))
        self.location = Location.add_root(name='Engine bay')
        self.item = InventoryItem.objects.create(name='Impeller', location=self.location)

    def attach(self, model, name='photo.jpg', **owner):
        photo = model(**owner)
        photo.image.save(name, ContentFile(make_jpeg((100, 100))))
        return photo.image.path

    def post(self, url_name, pk):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse(url_name, args=[pk]))

    def test_removing_a_photo_deletes_its_file_and_empty_folder(self):
        path = self.attach(ItemPhoto, item=self.item)
        self.post('inventory:item_photo_delete', ItemPhoto.objects.get().pk)
        self.assertFalse(os.path.exists(path))
        self.assertFalse(os.path.exists(os.path.dirname(path)))

    def test_folder_kept_while_other_photos_remain(self):
        gone = self.attach(ItemPhoto, 'a.jpg', item=self.item)
        kept = self.attach(ItemPhoto, 'b.jpg', item=self.item)
        self.post('inventory:item_photo_delete', ItemPhoto.objects.get(image__endswith='a.jpg').pk)
        self.assertFalse(os.path.exists(gone))
        self.assertTrue(os.path.exists(kept))

    def test_deleting_an_item_deletes_its_photos_and_its_spares_photos(self):
        item_photo = self.attach(ItemPhoto, item=self.item)
        spare = Spare.objects.create(item=self.item, location=self.location, name='Spare impeller')
        spare_photo = self.attach(SparePhoto, spare=spare)
        self.post('inventory:item_delete', self.item.pk)
        self.assertFalse(InventoryItem.objects.exists())
        self.assertFalse(os.path.exists(item_photo))
        self.assertFalse(os.path.exists(spare_photo))

    def test_deleting_a_location_deletes_photos_of_it_and_its_sublocations(self):
        cockpit = Location.add_root(name='Cockpit')
        locker = cockpit.add_child(name='Port locker')
        parent_photo = self.attach(LocationPhoto, location=cockpit)
        child_photo = self.attach(LocationPhoto, location=locker)
        self.post('inventory:location_delete', cockpit.pk)
        self.assertFalse(Location.objects.filter(name='Port locker').exists())
        self.assertFalse(os.path.exists(parent_photo))
        self.assertFalse(os.path.exists(child_photo))

    def test_blocked_location_delete_keeps_photos(self):
        # The location still has an item in it, so the delete is refused
        # (PROTECT) — its photo must survive.
        path = self.attach(LocationPhoto, location=self.location)
        self.post('inventory:location_delete', self.location.pk)
        self.assertTrue(Location.objects.filter(pk=self.location.pk).exists())
        self.assertTrue(os.path.exists(path))

    def test_deleting_a_repair_deletes_its_photos(self):
        repair = Repair.objects.create(title='Replace impeller', date='2026-09-26')
        path = self.attach(RepairPhoto, repair=repair)
        self.post('inventory:repair_delete', repair.pk)
        self.assertFalse(os.path.exists(path))

    def test_deleting_a_catalog_deletes_its_pdf(self):
        catalog = CatalogSource(name='4JH3E parts')
        catalog.file.save('4jh3e.pdf', ContentFile(b'%PDF-1.4'))
        path = catalog.file.path
        self.post('inventory:catalog_source_delete', catalog.pk)
        self.assertFalse(os.path.exists(path))

    def test_replacing_a_catalog_pdf_deletes_the_old_one(self):
        catalog = CatalogSource(name='4JH3E parts')
        catalog.file.save('old.pdf', ContentFile(b'%PDF-1.4 old'))
        old_path = catalog.file.path
        with self.captureOnCommitCallbacks(execute=True):
            catalog.file.save('new.pdf', ContentFile(b'%PDF-1.4 new'))
        self.assertFalse(os.path.exists(old_path))
        self.assertTrue(os.path.exists(catalog.file.path))

    def test_file_kept_if_the_delete_rolls_back(self):
        path = self.attach(ItemPhoto, item=self.item)
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            ItemPhoto.objects.get().delete()
        # The transaction never committed, so the queued delete never ran.
        self.assertEqual(len(callbacks), 1)
        self.assertTrue(os.path.exists(path))


class CleanMediaCommandTests(TestCase):
    def test_lists_then_deletes_only_unreferenced_files(self):
        media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media_root, ignore_errors=True)
        with override_settings(MEDIA_ROOT=media_root):
            item = InventoryItem.objects.create(name='Impeller', location=Location.add_root(name='Engine bay'))
            photo = ItemPhoto(item=item)
            photo.image.save('kept.jpg', ContentFile(make_jpeg((100, 100))))
            orphan = os.path.join(media_root, 'items', '999', 'stray.jpg')
            os.makedirs(os.path.dirname(orphan))
            with open(orphan, 'wb') as f:
                f.write(b'x')

            out = io.StringIO()
            call_command('clean_media', stdout=out)
            self.assertIn('items/999/stray.jpg', out.getvalue())
            self.assertNotIn('kept.jpg', out.getvalue())
            self.assertTrue(os.path.exists(orphan))

            call_command('clean_media', '--delete', stdout=io.StringIO())
            self.assertFalse(os.path.exists(os.path.dirname(orphan)))
            self.assertTrue(os.path.exists(photo.image.path))


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


class CatalogPartPickerTests(TestCase):
    """The catalog part picker matches on label text, so two BOM lines with
    the same part number and description in one fig (e.g. No.14 and its
    discontinued No.14-1) must still get different labels."""

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user('simon', password='pw'))
        catalog = CatalogSource.objects.create(name='Yanmar 4JH3E', file='catalogs/4jh3e.pdf')
        fig = CatalogSection.add_root(catalog=catalog, name='COOLING FRESH WATER COOLER', fig_number='28')
        self.current = CatalogPart.objects.create(
            section=fig, item_no='14', part_number='129270-44490', description='SEAL', remarks='W',
        )
        self.discontinued = CatalogPart.objects.create(
            section=fig, item_no='14-1', part_number='129270-44490', description='SEAL', remarks='Z',
        )

    def labels(self):
        return {o['id']: o['label'] for o in catalog_part_field(PartRequirementForm()['catalog_part'])['options']}

    def test_labels_include_fig_item_no_and_remark(self):
        labels = self.labels()
        self.assertEqual(
            labels[self.current.pk],
            '129270-44490 — SEAL · No.14 · W · Fig.28 COOLING FRESH WATER COOLER · Yanmar 4JH3E',
        )
        self.assertEqual(
            labels[self.discontinued.pk],
            '129270-44490 — SEAL · No.14-1 · Z · Fig.28 COOLING FRESH WATER COOLER · Yanmar 4JH3E',
        )

    def test_labels_stay_unique_when_lines_are_identical(self):
        CatalogPart.objects.filter(pk=self.discontinued.pk).update(item_no='14', remarks='W')
        labels = self.labels()
        self.assertEqual(len(set(labels.values())), 2)
        self.assertTrue(labels[self.discontinued.pk].endswith(f' #{self.discontinued.pk}'))

    def test_editing_a_requirement_links_the_chosen_line(self):
        requirement = PartRequirement.objects.create(
            job=Job.objects.create(title='Heat exchanger'), name='SEAL', catalog_part=self.discontinued,
        )
        self.client.post(reverse('inventory:requirement_edit', args=[requirement.pk]), {
            'name': 'SEAL', 'part_number': '129270-44490', 'quantity_needed': '1',
            'catalog_part': self.current.pk,
        })
        requirement.refresh_from_db()
        self.assertEqual(requirement.catalog_part, self.current)
