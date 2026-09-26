import re
import subprocess

from django.core.management.base import BaseCommand, CommandError

from inventory.models import CatalogPart, CatalogSection, CatalogSource

# Matches a section header line, e.g. "Fig.27. COOLING FRESH WATER PUMP" —
# the -layout text dump sometimes tacks the engine-variant legend
# ("(B)=4JH3CE  (E)=4JH3-TCE") onto the same line, stripped off separately.
FIG_RE = re.compile(r'^Fig\.(\S+?)\.\s+(.+?)\s*$', re.MULTILINE)
LEGEND_TAIL_RE = re.compile(r'\s{2,}\(.*$')

# Matches one BOM row, e.g. "12-1   3   129001-01250   PLUG, 50   1  1  1   N"
# columns: item no. / BOM level / part number / description / everything else
# (per-variant quantities plus the manual's own I/R remark flags).
ROW_RE = re.compile(r'^\s*(\d+(?:-\d+)?)\s+(\d+)\s+(\S+)\s+(.+?)\s{2,}(.*?)\s*$')

# A lone letter code (e.g. "N", "R", "W", "Z") trailing the quantity columns
# is the manual's remark flag, not a quantity — split it off when present.
TRAILING_REMARK_RE = re.compile(r'^(.*?)\s{2,}([A-Z]{1,2})$')


class Command(BaseCommand):
    help = (
        'Import a parts-catalog PDF (with a real, selectable text layer — not a scan) into '
        'CatalogSource/CatalogSection/CatalogPart. Re-running is safe: existing sections/parts '
        'are matched by fig number / item number and updated in place, not duplicated.'
    )

    def add_arguments(self, parser):
        parser.add_argument('pdf_path', help='Path to the catalog PDF.')
        parser.add_argument('--name', required=True, help='Name for the CatalogSource, e.g. "Yanmar 4JH3E".')
        parser.add_argument('--manufacturer', default='', help='e.g. "Yanmar".')
        parser.add_argument('--model-code', default='', help='e.g. "4JH3E".')

    def handle(self, *args, **options):
        pdf_path = options['pdf_path']
        try:
            result = subprocess.run(
                ['pdftotext', '-layout', pdf_path, '-'],
                capture_output=True, text=True, check=True,
            )
        except FileNotFoundError:
            raise CommandError('pdftotext (poppler-utils) is required but was not found on PATH.')
        except subprocess.CalledProcessError as exc:
            raise CommandError(f'pdftotext failed: {exc.stderr}')

        catalog, created = CatalogSource.objects.get_or_create(
            name=options['name'],
            defaults={'manufacturer': options['manufacturer'], 'model_code': options['model_code']},
        )
        if not created:
            catalog.manufacturer = options['manufacturer'] or catalog.manufacturer
            catalog.model_code = options['model_code'] or catalog.model_code
        if not catalog.file:
            catalog.file.name = self._relative_media_path(pdf_path)
        catalog.save()

        pages = result.stdout.split('\f')
        sections_touched = 0
        parts_touched = 0
        unparsed_candidates = []

        for page_number, page_text in enumerate(pages, start=1):
            fig_match = FIG_RE.search(page_text)
            if not fig_match:
                continue
            fig_number, fig_name = fig_match.groups()
            fig_name = LEGEND_TAIL_RE.sub('', fig_name).strip()

            # Every fig in this catalog sits at the top level (no deeper grouping in the
            # source manual), so sections are always created as tree roots. treebeard
            # nodes must go through add_root()/add_child(), not a plain create/save.
            section = CatalogSection.objects.filter(catalog=catalog, fig_number=fig_number).first()
            if section is None:
                section = CatalogSection.add_root(
                    catalog=catalog, name=fig_name, fig_number=fig_number, page_number=page_number,
                )
            sections_touched += 1

            for line in page_text.splitlines():
                if not line.strip():
                    continue
                row_match = ROW_RE.match(line)
                if not row_match:
                    if re.match(r'^\s*\d+(-\d+)?\s+\d+\s+\S', line):
                        unparsed_candidates.append((page_number, line))
                    continue
                item_no, bom_level, part_number, description, rest = row_match.groups()
                remark_match = TRAILING_REMARK_RE.match(rest)
                if remark_match:
                    quantity_note, remarks = remark_match.groups()
                else:
                    quantity_note, remarks = rest, ''
                quantity_note = re.sub(r'\s{2,}', '  ', quantity_note.strip())

                CatalogPart.objects.update_or_create(
                    section=section, item_no=item_no, part_number=part_number,
                    defaults={
                        'bom_level': int(bom_level),
                        'description': description.strip(),
                        'quantity_note': quantity_note,
                        'remarks': remarks,
                    },
                )
                parts_touched += 1

        self.stdout.write(self.style.SUCCESS(
            f'Imported "{catalog.name}": {sections_touched} section row(s) processed, '
            f'{parts_touched} part row(s) processed.'
        ))
        if unparsed_candidates:
            self.stdout.write(self.style.WARNING(
                f'{len(unparsed_candidates)} line(s) looked like part rows but did not parse — review these:'
            ))
            for page_number, line in unparsed_candidates[:20]:
                self.stdout.write(f'  page {page_number}: {line!r}')

    @staticmethod
    def _relative_media_path(pdf_path):
        """If the PDF already lives under MEDIA_ROOT, point the FileField at it in place
        instead of copying — catalog PDFs are dropped into media/catalogs/ by hand."""
        from pathlib import Path

        from django.conf import settings

        abs_path = Path(pdf_path).resolve()
        media_root = Path(settings.MEDIA_ROOT).resolve()
        try:
            return str(abs_path.relative_to(media_root))
        except ValueError:
            raise CommandError(
                f'{pdf_path} is not under MEDIA_ROOT ({media_root}) — move it into '
                'media/catalogs/ first so it can be served, then re-run.'
            )
