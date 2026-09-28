import io
import os
import shutil
import subprocess
import sys
import tempfile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .fitment import FITS, OTHER_BUILD, UNKNOWN, fitment_for, identifier_key
from .forms import PartRequirementForm
from .models import (
    CatalogPart, CatalogPartFitment, CatalogSection, CatalogSource, CatalogVariant, Equipment, InventoryItem, ItemPhoto, Job, Location, LocationPhoto,
    PartRequirement, Repair, RepairPhoto, Spare, SparePhoto,
)
from .management.commands.import_catalog import Command as ImportCatalogCommand
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


class EquipmentTests(TestCase):
    """Equipment records what you own (catalog, variant, serial) so catalog
    parts can later be checked against it."""

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user('simon', password='pw'))
        self.catalog = CatalogSource.objects.create(name='Yanmar 4JH3E', file='catalogs/4jh3e.pdf')
        self.variant = CatalogVariant.objects.create(catalog=self.catalog, code='A', name='4JH3E')
        self.other_catalog = CatalogSource.objects.create(name='Hull manual', file='catalogs/hull.pdf')
        self.other_variant = CatalogVariant.objects.create(catalog=self.other_catalog, code='S', name='Sloop')

    def post_equipment(self, **data):
        return self.client.post(reverse('inventory:equipment_add'), {'name': 'Main engine', **data})

    def test_add_equipment_fills_in_the_catalog_from_the_variant(self):
        self.post_equipment(variant=self.variant.pk, serial='E23123')
        equipment = Equipment.objects.get()
        self.assertEqual((equipment.catalog, equipment.variant, equipment.serial), (self.catalog, self.variant, 'E23123'))

    def test_variant_from_another_catalog_is_rejected(self):
        response = self.post_equipment(catalog=self.catalog.pk, variant=self.other_variant.pk)
        self.assertContains(response, 'That variant belongs to a different catalog.')
        self.assertFalse(Equipment.objects.exists())

    def test_job_links_to_equipment_and_deleting_it_keeps_the_job(self):
        equipment = Equipment.objects.create(name='Main engine', variant=self.variant, catalog=self.catalog)
        self.client.post(reverse('inventory:job_add'), {
            'title': 'Heat exchanger', 'status': Job.STATUS_PLANNING, 'equipment': equipment.pk,
        })
        job = Job.objects.get()
        self.assertEqual(job.equipment, equipment)
        self.assertContains(self.client.get(reverse('inventory:equipment_detail', args=[equipment.pk])), 'Heat exchanger')
        self.client.post(reverse('inventory:equipment_delete', args=[equipment.pk]))
        job.refresh_from_db()
        self.assertIsNone(job.equipment)

    def test_fitment_variant_must_match_the_parts_catalog(self):
        fig = CatalogSection.add_root(catalog=self.catalog, name='COOLING FRESH WATER COOLER', fig_number='28')
        part = CatalogPart.objects.create(section=fig, item_no='22', part_number='24321-000800')
        with self.assertRaises(ValidationError):
            CatalogPartFitment(part=part, variant=self.other_variant).full_clean()
        CatalogPartFitment(part=part, variant=self.variant, quantity=2, to_identifier='E25002').full_clean()


class FitmentTests(TestCase):
    """fitment_for() against a slice of Fig.28 of the Yanmar 4JH3E manual,
    as worked out by hand for engine E23123 (a 4JH3E, variant A)."""

    def setUp(self):
        self.catalog = CatalogSource.objects.create(name='Yanmar 4JH3E', file='catalogs/4jh3e.pdf')
        self.a = CatalogVariant.objects.create(catalog=self.catalog, code='A', name='4JH3E')
        self.c = CatalogVariant.objects.create(catalog=self.catalog, code='C', name='4JH3CE1')
        self.fig = CatalogSection.add_root(catalog=self.catalog, name='COOLING FRESH WATER COOLER', fig_number='28')
        self.engine = Equipment.objects.create(name='Main engine', catalog=self.catalog, variant=self.a, serial='E23123')

    def part(self, item_no, *fitments):
        part = CatalogPart.objects.create(section=self.fig, item_no=item_no, part_number=f'P-{item_no}')
        for variant, quantity, start, end in fitments:
            CatalogPartFitment.objects.create(
                part=part, variant=variant, quantity=quantity, from_identifier=start, to_identifier=end,
            )
        return part

    def test_original_and_updated_builds_either_side_of_the_change(self):
        o_ring = self.part('22', (self.a, 2, '', 'E25002'))
        o_ring_new = self.part('22-1', (self.a, 2, 'E25003', ''))
        fit = fitment_for(o_ring, self.engine)
        self.assertEqual((fit.status, fit.quantity), (FITS, 2))
        self.assertEqual(fitment_for(o_ring_new, self.engine).status, OTHER_BUILD)
        self.assertEqual(fitment_for(o_ring_new, self.engine).reason, 'from E25003')

    def test_open_ended_row_fits_any_serial(self):
        seal = self.part('14', (self.a, 1, '', ''))
        self.assertEqual(fitment_for(seal, self.engine).status, FITS)

    def test_variant_with_no_row_is_not_used(self):
        cock = self.part('16', (self.c, 1, '', ''))
        fit = fitment_for(cock, self.engine)
        self.assertEqual((fit.status, fit.reason), (OTHER_BUILD, 'not used on 4JH3E'))

    def test_serials_compare_numerically_and_ignore_punctuation(self):
        self.assertLess(identifier_key('E9999'), identifier_key('E10000'))
        self.assertEqual(identifier_key('E/#23803'), identifier_key('E23803'))
        self.engine.serial = 'E9999'
        part = self.part('1', (self.a, 1, '', 'E10000'))
        self.assertEqual(fitment_for(part, self.engine).status, FITS)

    def test_unknown_when_it_cannot_tell(self):
        part = self.part('22-1', (self.a, 2, 'E25003', ''))
        self.assertEqual(fitment_for(self.part('99'), self.engine).status, UNKNOWN)  # no fitment data
        self.engine.serial = ''
        self.assertEqual(fitment_for(part, self.engine).reason, 'depends on serial (from E25003)')
        self.engine.serial = '23123'  # typed without its E: don't guess
        self.assertEqual(fitment_for(part, self.engine).status, UNKNOWN)
        self.engine.variant = None
        self.assertEqual(fitment_for(part, self.engine).status, UNKNOWN)

    def test_prefetched_fitments_avoid_a_query_per_part(self):
        for item_no in ('22', '22-1', '23'):
            self.part(item_no, (self.a, 2, '', ''))
        parts = CatalogPart.objects.select_related('section').prefetch_related('fitments')
        with self.assertNumQueries(2):
            [fitment_for(p, self.engine) for p in parts]


class Fig28ImportMixin:
    """Imports real -layout text from Fig.28 of the Yanmar 4JH3E manual
    (inventory/test_data_fig28.txt)."""

    def setUp(self):
        with open(os.path.join(os.path.dirname(__file__), 'test_data_fig28.txt')) as f:
            self.text = f.read()
        self.catalog = CatalogSource.objects.create(name='Yanmar 4JH3E', file='catalogs/4jh3e.pdf')

    def run_import(self, dry_run=False):
        out = io.StringIO()
        command = ImportCatalogCommand(stdout=out)
        with transaction.atomic():
            command.import_text(self.text, 'unused.pdf', {
                'name': 'Yanmar 4JH3E', 'manufacturer': '', 'model_code': '', 'dry_run': dry_run,
            })
            if dry_run:
                transaction.set_rollback(True)
        return out.getvalue()


class ImportCatalogFitmentTests(Fig28ImportMixin, TestCase):
    """The importer on Fig.28, checked against the answers worked out by
    hand for engine E23123."""

    def engine(self, serial='E23123'):
        return Equipment(name='Main engine', catalog=self.catalog, serial=serial,
                         variant=CatalogVariant.objects.get(catalog=self.catalog, code='A'))

    def fit(self, item_no, serial='E23123'):
        return fitment_for(CatalogPart.objects.get(item_no=item_no), self.engine(serial))

    def test_variants_and_remark_codes_come_from_the_page(self):
        self.run_import()
        self.assertEqual(
            list(self.catalog.variants.values_list('code', 'name')),
            [('A', '4JH3E'), ('B', '4JH3CE'), ('C', '4JH3CE1'), ('D', '4JH3-TE'), ('E', '4JH3-TCE')],
        )
        self.assertIn('Not interchangeable', self.catalog.remark_codes.get(code='S').meaning)

    def test_original_build_parts_fit_e23123_and_updated_ones_do_not(self):
        self.run_import()
        o_ring = self.fit('22')
        self.assertEqual((o_ring.status, o_ring.quantity), (FITS, 2))
        self.assertEqual((self.fit('22-1').status, self.fit('22-1').reason), (OTHER_BUILD, 'from E25003'))
        self.assertEqual(self.fit('13').status, FITS)
        self.assertEqual(self.fit('13-1').status, OTHER_BUILD)
        self.assertEqual(self.fit('18').status, FITS)  # Z: dropped at E25003
        self.assertEqual(self.fit('18', serial='E25003').status, OTHER_BUILD)

    def test_n_coded_replacement_fits_older_engines_too(self):
        self.run_import()
        self.assertEqual((self.fit('16').status, self.fit('16').reason), (OTHER_BUILD, 'up to E21538'))
        self.assertEqual(self.fit('16-1').status, FITS)
        self.assertEqual(self.fit('16-1', serial='E20000').status, FITS)
        # Item 16 has no C or E quantity: those engines never used it.
        self.assertEqual(
            set(CatalogPart.objects.get(item_no='16').fitments.values_list('variant__code', flat=True)),
            {'A', 'B', 'D'},
        )

    def test_reimport_updates_in_place_and_keeps_hand_entered_data(self):
        self.run_import()
        o_ring = CatalogPart.objects.get(item_no='22')
        variant_a = CatalogVariant.objects.get(catalog=self.catalog, code='A')
        variant_a.name = 'My 4JH3E'
        variant_a.save()
        CatalogPartFitment.objects.create(part=o_ring, variant=variant_a, quantity=3, source=CatalogPartFitment.SOURCE_MANUAL)
        imported = o_ring.fitments.filter(source=CatalogPartFitment.SOURCE_IMPORT).count()

        out = self.run_import()

        self.assertEqual(CatalogPart.objects.get(item_no='22').pk, o_ring.pk)
        self.assertEqual(CatalogVariant.objects.get(pk=variant_a.pk).name, 'My 4JH3E')
        self.assertIn('Variant A is "My 4JH3E" here but "4JH3E" in the manual', out)
        self.assertEqual(o_ring.fitments.filter(source=CatalogPartFitment.SOURCE_MANUAL).count(), 1)
        self.assertEqual(o_ring.fitments.filter(source=CatalogPartFitment.SOURCE_IMPORT).count(), imported)

    def test_dry_run_saves_nothing(self):
        self.run_import(dry_run=True)
        self.assertFalse(CatalogPart.objects.exists())
        self.assertFalse(self.catalog.variants.exists())


class FitMarksTests(Fig28ImportMixin, TestCase):
    """The ✓ / ✗ marks on the job table, requirement page, part picker and
    catalog section page, for the heat exchanger job on engine E23123."""

    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_user('simon', password='pw'))
        self.run_import()
        self.engine = Equipment.objects.create(
            name='Main engine', catalog=self.catalog, serial='E23123',
            variant=CatalogVariant.objects.get(catalog=self.catalog, code='A'),
        )
        self.job = Job.objects.create(title='Heat exchanger', equipment=self.engine)
        self.o_ring = PartRequirement.objects.create(
            job=self.job, name='O-ring', catalog_part=CatalogPart.objects.get(item_no='22'),
        )
        self.cock = PartRequirement.objects.create(
            job=self.job, name='Cock', catalog_part=CatalogPart.objects.get(item_no='16'),
        )

    def test_job_table_marks_each_line(self):
        response = self.client.get(reverse('inventory:job_detail', args=[self.job.pk]))
        self.assertContains(response, 'Fits Main engine (E23123): up to E25002, qty 2')
        self.assertContains(response, 'Other build for Main engine (E23123): up to E21538')

    def test_no_marks_without_equipment(self):
        self.job.equipment = None
        self.job.save()
        response = self.client.get(reverse('inventory:job_detail', args=[self.job.pk]))
        self.assertNotContains(response, 'class="fit-badge')

    def test_requirement_page_spells_out_the_fit(self):
        response = self.client.get(reverse('inventory:requirement_detail', args=[self.cock.pk]))
        self.assertContains(response, '✗ Other build for Main engine (E23123): up to E21538')

    def test_picker_marks_parts_and_lists_fitting_ones_first(self):
        field = PartRequirementForm(instance=self.o_ring)['catalog_part']
        options = catalog_part_field(field, self.engine)['options']
        labels = [o['label'] for o in options]
        self.assertTrue(labels[0].startswith('✓ '))
        o_ring_new = next(label for label in labels if 'No.22-1' in label)
        self.assertTrue(o_ring_new.startswith('✗ '))
        self.assertLess(labels.index(next(label for label in labels if 'No.22 ' in label)), labels.index(o_ring_new))

    def test_section_page_checks_fit_and_explains_remarks(self):
        fig = CatalogSection.objects.get(fig_number='28')
        response = self.client.get(reverse('inventory:catalog_section_detail', args=[fig.pk]))
        self.assertContains(response, 'Other build for Main engine (E23123): from E25003')
        self.assertContains(response, '<abbr title="Not interchangeable either way')
        response = self.client.get(reverse('inventory:catalog_section_detail', args=[fig.pk]), {'equipment': 'none'})
        self.assertNotContains(response, 'class="fit-badge')
