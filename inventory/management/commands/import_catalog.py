import re
import subprocess
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from inventory.models import (
    CatalogPart,
    CatalogPartFitment,
    CatalogRemarkCode,
    CatalogSection,
    CatalogSource,
    CatalogVariant,
)

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

# The variant legend at the top of each page, e.g. "(A)=4JH3E   (D)=4JH3-TE".
LEGEND_RE = re.compile(r'\(([A-F])\)=(\S+)')
# The Q'ty column headings, e.g. "(A)     (B)   (C) (D)     (E)   (F)" — their
# character positions in the -layout text say which variant a quantity is for.
QTY_HEADER_RE = re.compile(r'\(A\)\s+\(B\)')
QTY_COLUMN_RE = re.compile(r'\(([A-F])\)')
# How far (in characters) a quantity may sit from its column heading.
QTY_COLUMN_TOLERANCE = 3

# A serial change-over line under a BOM row, e.g.
# "(A=E25003)  (B=E25003)  (C=E25003)  (D=E12191)  (E=E12191)". Values can be
# serials (E25003, E/#23803), production dates (*2004.04), FROM 1ST, or
# placeholders with no number yet (EZZZZZ).
SERIAL_RE = re.compile(r'\(([A-F])=([^)]*)\)')
FROM_FIRST = 'FROM 1ST'

# The legend printed at the foot of every Yanmar page, spelled out.
YANMAR_REMARK_CODES = {
    'N': 'Old → new: yes; new → old: no. The new part also fits older engines.',
    'Q': 'Old → new: no; new → old: yes. The old part also fits newer engines.',
    'R': 'Interchangeable both ways.',
    'S': 'Not interchangeable either way — match what is fitted.',
    'W': 'Added (appeared with a design change).',
    'Z': 'Discontinued (dropped with a design change).',
    'F': 'Interchangeable only as a set.',
    'K': 'Quantity changed.',
}
# On the newer line of a pair (e.g. 22-1 after 22), which codes mean the change
# doesn't limit fitment: N's new part also fits older engines (no lower bound);
# Q's old part also fits newer engines (no upper bound on the old line).
NEW_FITS_OLDER = {'N', 'R'}
OLD_FITS_NEWER = {'Q', 'R'}


@dataclass
class Row:
    """One parsed BOM row, kept until the whole fig is read so change-overs
    can be worked out across the pair (22 / 22-1)."""
    part: CatalogPart
    item_no: str
    remark: str
    quantities: dict | None  # variant code -> Decimal; None if the columns couldn't be read
    serials: dict = field(default_factory=dict)  # variant code -> change-over value

    @property
    def base(self):
        return self.item_no.split('-')[0]


def one_before(value):
    """The serial/date just before a change-over, for an inclusive upper
    bound: E25003 -> E25002, *2004.04 -> *2004.03. Placeholders with no
    trailing number (EZZZZZ) are kept as-is, which fitment treats as
    unknown rather than guessing."""
    match = re.match(r'^(.*?)(\d+)$', value)
    if not match or int(match.group(2)) == 0:
        return value
    prefix, digits = match.groups()
    return f'{prefix}{int(digits) - 1:0{len(digits)}d}'


def fitment_bounds(rows):
    """(from, to) per variant for each row of one fig, from the manual's own
    change-over lines. A change-over line belongs to the row above it:
      - on a Z (discontinued) row it's when the part stopped: an upper bound;
      - on any other row it's when the part started: a lower bound, and it
        caps the older line of the same No. (22 before 22-1) just below it.
    N/Q/R relax those limits (see NEW_FITS_OLDER / OLD_FITS_NEWER)."""
    bounds = {id(row): {code: ['', ''] for code in row.quantities or {}} for row in rows}
    for row in rows:
        for code, value in row.serials.items():
            if code not in bounds[id(row)] or value == FROM_FIRST:
                continue
            if row.remark == 'Z':
                bounds[id(row)][code][1] = one_before(value)
            else:
                bounds[id(row)][code][0] = value

    for i, row in enumerate(rows):
        if '-' not in row.item_no or row.remark == 'Z':
            continue
        older = next(
            (r for r in reversed(rows[:i]) if r.base == row.base and r.remark != 'Z'),
            None,
        )
        for code, value in row.serials.items():
            if code not in bounds[id(row)] or value == FROM_FIRST:
                continue
            if older and code in bounds[id(older)] and not bounds[id(older)][code][1] \
                    and row.remark not in OLD_FITS_NEWER:
                bounds[id(older)][code][1] = one_before(value)
            if row.remark in NEW_FITS_OLDER:
                bounds[id(row)][code][0] = ''
    return bounds


class Command(BaseCommand):
    help = (
        'Import a parts-catalog PDF (with a real, selectable text layer — not a scan) into '
        'CatalogSource/CatalogSection/CatalogPart, plus its variants, remark codes and per-variant '
        'fitment. Re-running is an update, not a rebuild: sections/parts are matched by fig number / '
        'item number / part number and updated in place, hand-entered variants, remark codes and '
        'fitment rows are kept, and only previously imported fitment rows are replaced.'
    )

    def add_arguments(self, parser):
        parser.add_argument('pdf_path', help='Path to the catalog PDF.')
        parser.add_argument('--name', required=True, help='Name for the CatalogSource, e.g. "Yanmar 4JH3E".')
        parser.add_argument('--manufacturer', default='', help='e.g. "Yanmar".')
        parser.add_argument('--model-code', default='', help='e.g. "4JH3E".')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would change, then roll everything back.',
        )

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

        with transaction.atomic():
            self.import_text(result.stdout, pdf_path, options)
            if options['dry_run']:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING('Dry run: nothing was saved.'))

    def import_text(self, text, pdf_path, options):
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
        self.stdout.write(f'{"Creating" if created else "Updating"} catalog "{catalog.name}".')

        pages = text.split('\f')
        self.import_variants(catalog, pages)
        self.import_remark_codes(catalog)

        sections_touched = 0
        parts_created = parts_updated = 0
        unparsed_candidates = []
        unaligned_rows = []
        skipped_serial_lines = []
        rows_by_section = {}
        last_row = None
        last_fig = None

        for page_number, page_text in enumerate(pages, start=1):
            fig_match = FIG_RE.search(page_text)
            if not fig_match:
                continue
            fig_number, fig_name = fig_match.groups()
            fig_name = LEGEND_TAIL_RE.sub('', fig_name).strip()
            if fig_number != last_fig:
                last_row, last_fig = None, fig_number
            columns = self.qty_columns(page_text)

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
                if SERIAL_RE.search(line):
                    if SERIAL_RE.sub('', line).strip() or last_row is None:
                        # A footnote that happens to quote serials, or a change-over
                        # line with no row above it — don't pin it to the wrong part.
                        skipped_serial_lines.append((page_number, line))
                    else:
                        last_row.serials.update(SERIAL_RE.findall(line))
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

                part, part_created = CatalogPart.objects.update_or_create(
                    section=section, item_no=item_no, part_number=part_number,
                    defaults={
                        'bom_level': int(bom_level),
                        'description': description.strip(),
                        'quantity_note': quantity_note,
                        'remarks': remarks,
                    },
                )
                if part_created:
                    parts_created += 1
                else:
                    parts_updated += 1
                quantities = self.row_quantities(line, row_match.start(5), columns)
                if quantities is None:
                    unaligned_rows.append((page_number, line))
                last_row = Row(part=part, item_no=item_no, remark=remarks, quantities=quantities)
                rows_by_section.setdefault(section.pk, []).append(last_row)

        fitment_rows = self.write_fitments(catalog, rows_by_section)

        self.stdout.write(self.style.SUCCESS(
            f'Imported "{catalog.name}": {sections_touched} section row(s) processed, '
            f'{parts_created + parts_updated} part row(s) processed '
            f'({parts_created} new, {parts_updated} updated), {fitment_rows} fitment row(s) written.'
        ))
        self.warn_lines(unparsed_candidates, 'looked like part rows but did not parse')
        self.warn_lines(unaligned_rows, "had quantities that didn't line up with a variant column (no fitment written)")
        self.warn_lines(skipped_serial_lines, 'quoted serials but were not attached to a part')

    def import_variants(self, catalog, pages):
        """Add the legend's variants (A = 4JH3E, ...) that aren't there yet.
        Existing ones are matched by code and left as they are — a hand-entered
        name that disagrees with the manual is reported, not overwritten."""
        legend = {}
        for page_text in pages:
            for code, name in LEGEND_RE.findall(page_text):
                legend.setdefault(code, name)
        existing = {v.code: v for v in catalog.variants.all()}
        for order, code in enumerate(sorted(legend)):
            variant = existing.get(code)
            if variant is None:
                CatalogVariant.objects.create(catalog=catalog, code=code, name=legend[code], order=order)
                self.stdout.write(f'  Added variant {code} = {legend[code]}.')
            elif variant.name != legend[code]:
                self.stdout.write(self.style.WARNING(
                    f'  Variant {code} is "{variant.name}" here but "{legend[code]}" in the manual — '
                    'kept yours; fix it in admin if the manual is right.'
                ))

    def import_remark_codes(self, catalog):
        existing = set(catalog.remark_codes.values_list('code', flat=True))
        missing = [code for code in YANMAR_REMARK_CODES if code not in existing]
        CatalogRemarkCode.objects.bulk_create(
            CatalogRemarkCode(catalog=catalog, code=code, meaning=YANMAR_REMARK_CODES[code]) for code in missing
        )
        if missing:
            self.stdout.write(f'  Added remark codes {", ".join(missing)}.')

    @staticmethod
    def qty_columns(page_text):
        """Character position of each variant's Q'ty column heading on this
        page ({'A': 121, ...}), or {} if the page has no heading."""
        for line in page_text.splitlines():
            if QTY_HEADER_RE.search(line):
                return {m.group(1): m.start() + 1 for m in QTY_COLUMN_RE.finditer(line)}
        return {}

    @staticmethod
    def row_quantities(line, rest_start, columns):
        """{'A': Decimal('2'), ...} for one BOM row, placing each number under
        the nearest column heading — blank columns vanish from a whitespace
        split (item 16 is A, B and D only), so position is what counts.
        Numbers right of the last column are footnote references, not
        quantities. None if there's no heading or a number sits between
        columns."""
        if not columns:
            return None
        last_column = max(columns.values()) + QTY_COLUMN_TOLERANCE
        quantities = {}
        for token in re.finditer(r'\S+', line[rest_start:]):
            if not re.fullmatch(r'\d+(\.\d+)?', token.group()):
                continue  # the remark flag
            centre = rest_start + (token.start() + token.end() - 1) / 2
            if centre > last_column:
                continue  # a footnote number in the I/R columns, e.g. "(1) UNDER SIZE PART"
            code, position = min(columns.items(), key=lambda c: abs(c[1] - centre))
            if abs(position - centre) > QTY_COLUMN_TOLERANCE or code in quantities:
                return None
            quantities[code] = Decimal(token.group())
        return quantities

    @staticmethod
    def write_fitments(catalog, rows_by_section):
        """Replace each part's imported fitment rows; hand-entered ones stay."""
        variants = {v.code: v for v in catalog.variants.all()}
        new_rows = []
        replaced_parts = []
        for rows in rows_by_section.values():
            bounds = fitment_bounds(rows)
            for row in rows:
                if row.quantities is None:
                    continue
                replaced_parts.append(row.part.pk)
                for code, quantity in row.quantities.items():
                    if code not in variants:
                        continue
                    start, end = bounds[id(row)][code]
                    new_rows.append(CatalogPartFitment(
                        part=row.part, variant=variants[code], quantity=quantity,
                        from_identifier=start, to_identifier=end,
                        source=CatalogPartFitment.SOURCE_IMPORT,
                    ))
        CatalogPartFitment.objects.filter(
            part_id__in=replaced_parts, source=CatalogPartFitment.SOURCE_IMPORT,
        ).delete()
        CatalogPartFitment.objects.bulk_create(new_rows)
        return len(new_rows)

    def warn_lines(self, lines, what):
        if not lines:
            return
        self.stdout.write(self.style.WARNING(f'{len(lines)} line(s) {what} — review these:'))
        for page_number, line in lines[:20]:
            self.stdout.write(f'  page {page_number}: {line.strip()!r}')

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
