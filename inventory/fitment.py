"""Does a catalog part fit a particular piece of equipment?

Checks a part's CatalogPartFitment rows against the equipment's variant and
serial/hull number (or year). The answer is advisory: callers flag parts,
they never hide them, since a boat can drift from its book (e.g. a newer
cooler assembly fitted to an older engine).
"""
import re
from dataclasses import dataclass
from decimal import Decimal

from .utils import natural_key

FITS = 'fits'
OTHER_BUILD = 'other_build'
UNKNOWN = 'unknown'


@dataclass(frozen=True)
class Fit:
    status: str
    reason: str = ''
    quantity: Decimal | None = None


def identifier_key(value):
    """Natural sort key for a serial/hull number or year, ignoring
    punctuation, so the manual's 'E/#23803' compares equal to 'E23803' and
    E9999 sorts before E10000."""
    return natural_key(re.sub(r'[\W_]+', '', value or ''))


def _comparable(a, b):
    """Only compare identifiers made of the same letters in the same places
    — '23123' against 'E25003' (a serial typed without its prefix) would
    otherwise give a confident but meaningless answer."""
    def shape(key):
        return [tok[2] for tok in key if tok[0] == 1]
    return shape(a) == shape(b)


def describe_range(fitment):
    """'from E25003', 'up to E25002', 'E1000–E2000', or '' if open-ended."""
    start, end = fitment.from_identifier, fitment.to_identifier
    if start and end:
        return f'{start}–{end}'
    if start:
        return f'from {start}'
    if end:
        return f'up to {end}'
    return ''


def fitment_for(part, equipment, fitments=None):
    """Fit(status, reason, quantity) for one catalog part on one piece of
    equipment. Pass `fitments` (the part's CatalogPartFitment rows) when
    checking many parts at once; otherwise part.fitments.all() is used, which
    reads from a prefetch_related('fitments') cache when there is one."""
    if equipment is None or equipment.variant_id is None:
        return Fit(UNKNOWN, 'no model set for this equipment')
    variant = equipment.variant
    if part.section.catalog_id != variant.catalog_id:
        return Fit(UNKNOWN, 'part is from a different catalog')

    rows = list(part.fitments.all() if fitments is None else fitments)
    if not rows:
        return Fit(UNKNOWN, 'no fitment data for this part')
    mine = [row for row in rows if row.variant_id == variant.pk]
    if not mine:
        return Fit(OTHER_BUILD, f'not used on {variant.name}')

    serial = identifier_key(equipment.serial)
    ranges, cannot_compare = [], []
    for row in mine:
        bounds = [identifier_key(v) for v in (row.from_identifier, row.to_identifier) if v]
        if not bounds:
            return Fit(FITS, '', row.quantity)
        if not equipment.serial:
            ranges.append(describe_range(row))
            continue
        if not all(_comparable(serial, bound) for bound in bounds):
            cannot_compare.append(describe_range(row))
            continue
        low = identifier_key(row.from_identifier) if row.from_identifier else None
        high = identifier_key(row.to_identifier) if row.to_identifier else None
        if (low is None or serial >= low) and (high is None or serial <= high):
            return Fit(FITS, describe_range(row), row.quantity)
        ranges.append(describe_range(row))

    if not equipment.serial:
        return Fit(UNKNOWN, f'depends on serial ({"; ".join(ranges)})')
    if cannot_compare:
        return Fit(UNKNOWN, f"can't compare {equipment.serial} with {'; '.join(cannot_compare)}")
    return Fit(OTHER_BUILD, '; '.join(ranges))
