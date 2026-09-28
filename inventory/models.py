import os
from decimal import Decimal
from urllib.parse import quote

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models, transaction
from django.db.models.signals import post_delete, pre_save
from django.db.models.functions import Lower
from treebeard.mp_tree import MP_Node

from .images import shrink_photo

# Shared by ItemPhoto/LocationPhoto/RepairPhoto: photos plus PDFs (manuals,
# receipts, warranty docs) — PDFs are stored as-is rather than converted to
# an image, since that preserves multi-page/searchable/full-quality
# documents and browsers already render PDFs natively when opened.
ATTACHMENT_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'pdf']
validate_attachment_extension = FileExtensionValidator(allowed_extensions=ATTACHMENT_EXTENSIONS)


def format_quantity(quantity):
    """Render a decimal quantity without trailing zeros, e.g. 10.00 -> '10', 4.50 -> '4.5'."""
    text = f'{quantity:f}'
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text


class Location(MP_Node):
    """A place on the boat (e.g. Galley > Floor > Under Panel 3)."""

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    value = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Value of this location itself (e.g. the vessel's hull, or a structure "
                   "like built-in shelving), in $, if known. Leave blank for most locations "
                   '— this is separate from the value of what\'s stored here.',
    )

    node_order_by = ['name']

    class Meta:
        verbose_name = 'location'
        verbose_name_plural = 'locations'

    def __str__(self):
        return self.name

    @property
    def total_value(self):
        """Rolled-up value of this location and everything beneath it: its own
        (and any descendants') value, plus every item and spare stored anywhere
        in the subtree, each already qty x unit price."""
        subtree_ids = Location.get_tree(self).values_list('pk', flat=True)
        locations_total = Location.objects.filter(pk__in=subtree_ids).aggregate(
            total=models.Sum('value'))['total'] or 0
        item_value_expr = models.ExpressionWrapper(
            models.F('quantity') * models.F('unit_price'),
            output_field=models.DecimalField(max_digits=12, decimal_places=2),
        )
        items_total = InventoryItem.objects.filter(location_id__in=subtree_ids).aggregate(
            total=models.Sum(item_value_expr))['total'] or 0
        spares_total = Spare.objects.filter(location_id__in=subtree_ids).aggregate(
            total=models.Sum(item_value_expr))['total'] or 0
        return locations_total + items_total + spares_total


class ItemCategory(MP_Node):
    """A category for classifying inventory items (e.g. Tools > Hand Tools)."""

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    node_order_by = ['name']

    class Meta:
        verbose_name = 'item category'
        verbose_name_plural = 'item categories'

    def __str__(self):
        return self.name


class Unit(models.Model):
    """A unit of measure for quantity (e.g. mm, ml, kg)."""

    name = models.CharField(max_length=20, unique=True)

    class Meta:
        ordering = [Lower('name')]

    def __str__(self):
        return self.name


class InventoryItem(models.Model):
    CONDITION_GOOD = 'good'
    CONDITION_FAIR = 'fair'
    CONDITION_POOR = 'poor'
    CONDITION_NEEDS_REPAIR = 'needs_repair'
    CONDITION_CHOICES = [
        (CONDITION_GOOD, 'Good'),
        (CONDITION_FAIR, 'Fair'),
        (CONDITION_POOR, 'Poor'),
        (CONDITION_NEEDS_REPAIR, 'Needs repair'),
    ]

    name = models.CharField(max_length=200)
    category = models.ForeignKey(
        ItemCategory, on_delete=models.PROTECT, related_name='items',
        null=True, blank=True,
    )
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, related_name='items',
    )
    quantity = models.DecimalField(
        max_digits=10, decimal_places=2, default=1, validators=[MinValueValidator(0)],
        help_text='Usually a whole count, but can be fractional (e.g. 4.5) when tracking by a unit like L or kg.',
    )
    unit = models.ForeignKey(
        Unit, on_delete=models.PROTECT, related_name='items',
        null=True, blank=True,
    )
    condition = models.CharField(
        max_length=20, choices=CONDITION_CHOICES, default=CONDITION_GOOD, blank=True,
    )
    unit_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Price per unit, in $, if known. Useful for insurance/valuation.',
    )
    notes = models.TextField(blank=True)
    sourced_from = models.ForeignKey(
        'ProposalOption', on_delete=models.SET_NULL, related_name='inventory_items',
        null=True, blank=True,
        help_text='The purchase option this stock was ordered from, if it came in through the job/purchasing flow.',
    )
    catalog_part = models.ForeignKey(
        'CatalogPart', on_delete=models.SET_NULL, related_name='inventory_items',
        null=True, blank=True,
        help_text='The parts-catalog entry this item matches, if you have one on file.',
    )
    date_added = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def total_value(self):
        if self.unit_price is None:
            return None
        return (self.quantity * self.unit_price).quantize(Decimal('0.01'))


def item_photo_path(instance, filename):
    return f'items/{instance.item_id}/{filename}'


def location_photo_path(instance, filename):
    return f'locations/{instance.location_id}/{filename}'


class ItemPhoto(models.Model):
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='photos')
    image = models.FileField(upload_to=item_photo_path, validators=[validate_attachment_extension])
    caption = models.CharField(max_length=200, blank=True)
    is_primary = models.BooleanField(default=False)
    is_receipt = models.BooleanField(default=False, help_text='This is a purchase receipt, not a photo of the item.')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_primary', 'uploaded_at']

    def __str__(self):
        return f'Photo of {self.item.name}'

    @property
    def is_pdf(self):
        return self.image.name.lower().endswith('.pdf')

    def save(self, *args, **kwargs):
        shrink_photo(self.image)
        super().save(*args, **kwargs)


class Spare(models.Model):
    """A spare-parts kit for a specific inventory item (e.g. the spare washers
    that came with a sprayer). Linked to the item it belongs to, but tagged
    with its own location since a spares kit is often stowed apart from the
    item itself."""

    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='spares')
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name='spares')
    name = models.CharField(max_length=200)
    quantity = models.DecimalField(
        max_digits=10, decimal_places=2, default=1, validators=[MinValueValidator(0)],
        help_text='Usually a whole count, but can be fractional (e.g. 4.5) when tracking by a unit like L or kg.',
    )
    unit = models.ForeignKey(
        Unit, on_delete=models.PROTECT, related_name='spares',
        null=True, blank=True,
    )
    unit_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, blank=True,
        help_text='Price per unit, in $, if known.',
    )
    notes = models.TextField(blank=True)
    date_added = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} (spare for {self.item.name})'

    @property
    def total_value(self):
        return (self.quantity * self.unit_price).quantize(Decimal('0.01'))


def spare_photo_path(instance, filename):
    return f'spares/{instance.spare_id}/{filename}'


class SparePhoto(models.Model):
    spare = models.ForeignKey(Spare, on_delete=models.CASCADE, related_name='photos')
    image = models.FileField(upload_to=spare_photo_path, validators=[validate_attachment_extension])
    caption = models.CharField(max_length=200, blank=True)
    is_primary = models.BooleanField(default=False)
    is_receipt = models.BooleanField(default=False, help_text='This is a purchase receipt, not a photo of the spare.')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_primary', 'uploaded_at']

    def __str__(self):
        return f'Photo of {self.spare.name}'

    @property
    def is_pdf(self):
        return self.image.name.lower().endswith('.pdf')

    def save(self, *args, **kwargs):
        shrink_photo(self.image)
        super().save(*args, **kwargs)


class LocationPhoto(models.Model):
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='photos')
    image = models.FileField(upload_to=location_photo_path, validators=[validate_attachment_extension])
    caption = models.CharField(max_length=200, blank=True)
    is_primary = models.BooleanField(default=False)
    is_receipt = models.BooleanField(default=False, help_text='This is a purchase/valuation receipt, not a photo of the location.')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_primary', 'uploaded_at']

    def __str__(self):
        return f'Photo of {self.location.name}'

    @property
    def is_pdf(self):
        return self.image.name.lower().endswith('.pdf')

    def save(self, *args, **kwargs):
        shrink_photo(self.image)
        super().save(*args, **kwargs)


class LocationHotspot(models.Model):
    """
    A clickable region on a LocationPhoto that drills down into a child
    Location. Scaffolded now so the future image-map drill-down UI only
    needs new views/templates, not a schema change.
    """

    SHAPE_RECT = 'rect'
    SHAPE_POLYGON = 'poly'
    SHAPE_CHOICES = [
        (SHAPE_RECT, 'Rectangle'),
        (SHAPE_POLYGON, 'Polygon'),
    ]

    photo = models.ForeignKey(LocationPhoto, on_delete=models.CASCADE, related_name='hotspots')
    target_location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='hotspot_links')
    shape = models.CharField(max_length=10, choices=SHAPE_CHOICES, default=SHAPE_RECT)
    coordinates = models.JSONField(
        help_text='List of {x, y} points as percentages (0-100) of image width/height.',
    )
    label = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f'Hotspot on {self.photo} -> {self.target_location}'


class RepairCategory(models.Model):
    """A flat classification for repair log entries (e.g. Routine maintenance,
    Emergency repair). Unlike Location/ItemCategory this isn't a hierarchy —
    repair types don't nest, so a plain lookup table is enough."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = 'repair categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class Repair(models.Model):
    """A single ship's-log entry: a repair or service event, optionally
    consuming inventory items and carrying its own photos."""

    title = models.CharField(max_length=200)
    category = models.ForeignKey(
        RepairCategory, on_delete=models.SET_NULL, related_name='repairs',
        null=True, blank=True,
    )
    location = models.ForeignKey(
        Location, on_delete=models.SET_NULL, related_name='repairs',
        null=True, blank=True,
    )
    date = models.DateField()
    hours_spent = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True,
        help_text='Approx. hours spent, if you want to track it.',
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f'{self.title} ({self.date})'


def repair_photo_path(instance, filename):
    return f'repairs/{instance.repair_id}/{filename}'


class RepairPhoto(models.Model):
    repair = models.ForeignKey(Repair, on_delete=models.CASCADE, related_name='photos')
    image = models.FileField(upload_to=repair_photo_path, validators=[validate_attachment_extension])
    caption = models.CharField(max_length=200, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']

    def __str__(self):
        return f'Photo of {self.repair}'

    @property
    def is_pdf(self):
        return self.image.name.lower().endswith('.pdf')

    def save(self, *args, **kwargs):
        shrink_photo(self.image)
        super().save(*args, **kwargs)


class RepairConsumedItem(models.Model):
    """An inventory item (and quantity) used up in a repair. Consuming an
    item here decrements InventoryItem.quantity; deleting the record
    restores it (handled in the view, not here, so the stock adjustment
    stays visible/auditable rather than hidden in a signal)."""

    repair = models.ForeignKey(Repair, on_delete=models.CASCADE, related_name='consumed_items')
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name='consumptions')
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1, validators=[MinValueValidator(0)])

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{format_quantity(self.quantity)} x {self.item.name}'

    @property
    def cost(self):
        if self.item.unit_price is None:
            return None
        return (self.quantity * self.item.unit_price).quantize(Decimal('0.01'))


class Vendor(models.Model):
    """A supplier parts are bought from — kept separately from a one-off
    ProposalOption link so contact details and purchase history build up
    across jobs, not just within one."""

    name = models.CharField(max_length=200, unique=True)
    contact_name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    url = models.URLField(blank=True, verbose_name='Website')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Job(models.Model):
    """A piece of work (e.g. 'Engine hose replacement') that parts are
    bought for. Holds a checklist of PartRequirements, each compared across
    ProposalOptions before one is ordered."""

    STATUS_PLANNING = 'planning'
    STATUS_ORDERING = 'ordering'
    STATUS_COMPLETE = 'complete'
    STATUS_CHOICES = [
        (STATUS_PLANNING, 'Planning'),
        (STATUS_ORDERING, 'Ordering'),
        (STATUS_COMPLETE, 'Complete'),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    location = models.ForeignKey(
        Location, on_delete=models.SET_NULL, related_name='jobs',
        null=True, blank=True,
    )
    equipment = models.ForeignKey(
        'Equipment', on_delete=models.SET_NULL, related_name='jobs',
        null=True, blank=True,
        help_text='The engine, hull or gear this job is on — used to check catalog parts fit it.',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PLANNING)
    target_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    @property
    def ordered_cost(self):
        """Running total of what's actually been committed to (ordered/received
        options), computed live so it can't drift from the underlying rows.
        ordered_price is already the total paid for ordered_quantity (mirrors
        how price/quantity_per_purchase work on the option itself) — so this
        is a plain sum, not price x quantity."""
        total = ProposalOption.objects.filter(
            requirement__job=self,
            status__in=[ProposalOption.STATUS_ORDERED, ProposalOption.STATUS_RECEIVED],
        ).aggregate(total=models.Sum('ordered_price'))['total']
        return total or Decimal('0.00')


class PartRequirement(models.Model):
    """One actual part needed for a job (e.g. 'Upper radiator hose, qty 1')
    — the checklist row that ProposalOptions get compared against."""

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='requirements')
    name = models.CharField(max_length=200)
    part_number = models.CharField(
        max_length=100, blank=True,
        help_text='OEM/manufacturer part number, if known — makes it much easier to '
                   'match listings across vendors.',
    )
    category = models.ForeignKey(
        ItemCategory, on_delete=models.SET_NULL, related_name='part_requirements',
        null=True, blank=True,
    )
    quantity_needed = models.DecimalField(
        max_digits=10, decimal_places=2, default=1, validators=[MinValueValidator(0)],
    )
    unit = models.ForeignKey(
        Unit, on_delete=models.PROTECT, related_name='part_requirements',
        null=True, blank=True,
    )
    catalog_part = models.ForeignKey(
        'CatalogPart', on_delete=models.SET_NULL, related_name='part_requirements',
        null=True, blank=True,
        help_text='The parts-catalog entry this requirement matches, if you have one on file.',
    )
    rfq_vendor = models.ForeignKey(
        Vendor, on_delete=models.SET_NULL, related_name='rfq_requirements',
        null=True, blank=True,
        help_text='Vendor to request a quote from for this part — separate from ProposalOption.vendor, '
                   'which is for a specific priced listing once you have one.',
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return self.name

    @property
    def ordered_quantity(self):
        total = self.options.filter(
            status__in=[ProposalOption.STATUS_ORDERED, ProposalOption.STATUS_RECEIVED],
        ).aggregate(total=models.Sum('ordered_quantity'))['total']
        return total or Decimal('0')

    @property
    def is_fulfilled(self):
        return self.ordered_quantity >= self.quantity_needed

    @property
    def best_option(self):
        """The cheapest still-proposed option by unit price, for an at-a-glance
        'current best value' indicator while still comparing."""
        candidates = [o for o in self.options.all() if o.status == ProposalOption.STATUS_PROPOSED and o.unit_price is not None]
        if not candidates:
            return None
        return min(candidates, key=lambda o: o.unit_price)


class ProposalOption(models.Model):
    """One candidate purchase for a PartRequirement — a URL, a price and
    (optionally) a vendor — so several can be compared before ordering.
    Ordering one stamps the order number/confirmed price here and creates
    the actual InventoryItem it becomes (see views.option_order)."""

    STATUS_PROPOSED = 'proposed'
    STATUS_ORDERED = 'ordered'
    STATUS_RECEIVED = 'received'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_PROPOSED, 'Proposed'),
        (STATUS_ORDERED, 'Ordered'),
        (STATUS_RECEIVED, 'Received'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    requirement = models.ForeignKey(PartRequirement, on_delete=models.CASCADE, related_name='options')
    vendor = models.ForeignKey(
        Vendor, on_delete=models.PROTECT, related_name='proposal_options',
        null=True, blank=True,
    )
    url = models.URLField(blank=True)
    price = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text='Listed price for quantity_per_purchase, in $.',
    )
    quantity_per_purchase = models.DecimalField(
        max_digits=10, decimal_places=2, default=1, validators=[MinValueValidator(Decimal('0.01'))],
        help_text="How many units that price buys, e.g. 5 for a 'pack of 5' listing — "
                   'makes differently-sized listings comparable by unit price.',
    )
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PROPOSED)
    order_number = models.CharField(max_length=100, blank=True)
    ordered_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Total price actually paid for ordered_quantity, in $ — prefilled from the '
                   'listed price but editable if it changed.',
    )
    ordered_quantity = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='How many units were actually ordered for ordered_price.',
    )
    ordered_at = models.DateTimeField(null=True, blank=True)
    date_found = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['price']

    def __str__(self):
        return f'{self.requirement.name} — {self.vendor or "unlisted vendor"} (${self.price})'

    @property
    def unit_price(self):
        if not self.quantity_per_purchase:
            return None
        return (self.price / self.quantity_per_purchase).quantize(Decimal('0.01'))

    @property
    def total_ordered_cost(self):
        """ordered_price is already the total paid for ordered_quantity (see
        its help_text) — not a per-unit price, so no multiplication here."""
        if self.ordered_price is None:
            return None
        return self.ordered_price.quantize(Decimal('0.01'))


validate_pdf_extension = FileExtensionValidator(allowed_extensions=['pdf'])


def catalog_source_path(instance, filename):
    return f'catalogs/{filename}'


class CatalogSource(models.Model):
    """A single manufacturer/vendor parts manual (e.g. 'Yanmar 4JH3E') —
    holds the source PDF itself, so a catalog part can link straight back
    into the manual at the right page instead of duplicating its diagrams."""

    name = models.CharField(max_length=200, unique=True)
    manufacturer = models.CharField(max_length=200, blank=True)
    model_code = models.CharField(
        max_length=100, blank=True,
        help_text='Engine/equipment model this catalog covers, e.g. 4JH3E.',
    )
    file = models.FileField(upload_to=catalog_source_path, validators=[validate_pdf_extension])
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class CatalogSection(MP_Node):
    """One node in a catalog's own structure — typically a 'Fig.' exploded
    assembly diagram. A tree so a catalog with deeper grouping (system >
    sub-assembly > fig) fits the same model as a flat one, like 4JH3E,
    where every fig sits at the top level."""

    catalog = models.ForeignKey(CatalogSource, on_delete=models.CASCADE, related_name='sections')
    name = models.CharField(max_length=200, help_text="e.g. 'Fig.27 COOLING FRESH WATER PUMP'.")
    fig_number = models.CharField(
        max_length=20, blank=True,
        help_text="The catalog's own figure/section number, if it has one — used to order sections.",
    )
    page_number = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Page in the source PDF this section starts on, for the "open manual page" link.',
    )
    description = models.TextField(blank=True)

    node_order_by = ['fig_number', 'name']

    class Meta:
        verbose_name = 'catalog section'
        verbose_name_plural = 'catalog sections'

    def __str__(self):
        return self.name


class CatalogPart(models.Model):
    """One BOM line within a CatalogSection — an actual catalog part number.
    Keeps the manual's own item/level bookkeeping for reference, since it's
    printed on the diagram and useful when cross-checking by eye."""

    section = models.ForeignKey(CatalogSection, on_delete=models.CASCADE, related_name='parts')
    item_no = models.CharField(
        max_length=20, blank=True,
        help_text="The manual's own item number within this section, e.g. '12-1'.",
    )
    bom_level = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="The manual's own BOM indent level (its 'Lev.' column) — unrelated to the "
                   'catalog section tree depth above.',
    )
    part_number = models.CharField(max_length=100, db_index=True)
    description = models.CharField(max_length=300, blank=True)
    quantity_note = models.CharField(
        max_length=200, blank=True,
        help_text='Quantity per engine/equipment variant, as printed in the manual (free text, '
                   'since variant columns differ by catalog).',
    )
    remarks = models.CharField(
        max_length=100, blank=True,
        help_text="The manual's own remarks/flag column (e.g. superseded, interchangeable).",
    )

    class Meta:
        ordering = ['id']
        verbose_name = 'catalog part'
        verbose_name_plural = 'catalog parts'

    def __str__(self):
        return f'{self.part_number} — {self.description}' if self.description else self.part_number


class CatalogVariant(models.Model):
    """One model/configuration a catalog covers — the Yanmar manual's Q'ty
    columns, e.g. A = 4JH3E, D = 4JH3-TE. A hull or deck-gear catalog might
    have 'Sloop' / 'Cutter'. A catalog for a single model has just one."""

    catalog = models.ForeignKey(CatalogSource, on_delete=models.CASCADE, related_name='variants')
    code = models.CharField(max_length=10, help_text="The catalog's own column letter/code, e.g. 'A'.")
    name = models.CharField(max_length=100, help_text="e.g. '4JH3E'.")
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['catalog', 'order', 'code']
        constraints = [
            models.UniqueConstraint(fields=['catalog', 'code'], name='unique_catalog_variant_code'),
        ]

    def __str__(self):
        return f'{self.name} ({self.code})'


class CatalogRemarkCode(models.Model):
    """What a catalog's remarks letters mean (Yanmar: S = not interchangeable
    either way, Z = discontinued, ...) — each manufacturer has its own."""

    catalog = models.ForeignKey(CatalogSource, on_delete=models.CASCADE, related_name='remark_codes')
    code = models.CharField(max_length=10)
    meaning = models.CharField(max_length=300)

    class Meta:
        ordering = ['catalog', 'code']
        constraints = [
            models.UniqueConstraint(fields=['catalog', 'code'], name='unique_catalog_remark_code'),
        ]

    def __str__(self):
        return f'{self.code}: {self.meaning}'


class CatalogPartFitment(models.Model):
    """When a catalog part applies: on which variant, how many, and between
    which serial/hull numbers (or years). Several rows per part are fine; a
    variant with no row doesn't use the part. Identifiers are free text,
    compared in natural order (E9999 < E10000); blank = open-ended."""

    part = models.ForeignKey(CatalogPart, on_delete=models.CASCADE, related_name='fitments')
    variant = models.ForeignKey(CatalogVariant, on_delete=models.CASCADE, related_name='fitments')
    quantity = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    from_identifier = models.CharField(
        max_length=50, blank=True,
        help_text='First serial/hull number (or year) this applies to. Blank = from the first one.',
    )
    to_identifier = models.CharField(
        max_length=50, blank=True,
        help_text='Last serial/hull number (or year) this applies to. Blank = no end.',
    )

    class Meta:
        ordering = ['part', 'variant__order', 'from_identifier']
        verbose_name = 'catalog part fitment'

    def __str__(self):
        span = ''
        if self.from_identifier or self.to_identifier:
            span = f' {self.from_identifier or "first"}–{self.to_identifier or "on"}'
        return f'{self.part} on {self.variant.name}{span}'

    def clean(self):
        if self.part_id and self.variant_id and self.variant.catalog_id != self.part.section.catalog_id:
            raise ValidationError({'variant': "That variant belongs to a different catalog from this part."})


class Equipment(models.Model):
    """Something you own that a catalog describes — an engine, a hull, a
    winch — with its variant and serial/hull number, so catalog parts can be
    checked against the exact one you have."""

    name = models.CharField(max_length=200, help_text="e.g. 'Main engine'.")
    location = models.ForeignKey(
        Location, on_delete=models.SET_NULL, related_name='equipment',
        null=True, blank=True,
    )
    catalog = models.ForeignKey(
        CatalogSource, on_delete=models.SET_NULL, related_name='equipment',
        null=True, blank=True,
    )
    variant = models.ForeignKey(
        CatalogVariant, on_delete=models.SET_NULL, related_name='equipment',
        null=True, blank=True,
        help_text="Which of the catalog's models this is, e.g. 4JH3E (A).",
    )
    serial = models.CharField(
        max_length=100, blank=True,
        help_text='Engine serial number, hull identification number or model year — as the catalog uses it.',
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'equipment'

    def __str__(self):
        return self.name

    def clean(self):
        if self.variant_id:
            if not self.catalog_id:
                self.catalog_id = self.variant.catalog_id
            elif self.variant.catalog_id != self.catalog_id:
                raise ValidationError({'variant': "That variant belongs to a different catalog."})


class Rfq(models.Model):
    """A request-for-quote sent (or about to be sent) to one vendor for a
    batch of a job's part requirements — separate from ProposalOption, which
    only exists once a specific priced listing has been found. Vendor is
    nullable: an RFQ can be drafted for requirements with no vendor assigned
    yet, with the To: address left blank for you to fill in by hand."""

    STATUS_DRAFT = 'draft'
    STATUS_SENT = 'sent'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_SENT, 'Sent'),
    ]

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='rfqs')
    vendor = models.ForeignKey(
        Vendor, on_delete=models.SET_NULL, related_name='rfqs',
        null=True, blank=True,
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'RFQ'
        verbose_name_plural = 'RFQs'

    def __str__(self):
        return f'RFQ for {self.job.title} — {self.vendor or "no vendor"} ({self.get_status_display()})'

    @property
    def mailto_url(self):
        """A mailto: link with the vendor's email (if known), a subject, and a
        plain-text parts table in the body — mailto bodies can't carry real
        HTML, so this is padded into aligned columns instead. A few blank
        lines are left at the top for you to write a covering paragraph
        before the table."""
        lines = list(self.lines.select_related('requirement', 'requirement__unit'))
        name_width = max([len(line.requirement.name) for line in lines] + [len('Part')])
        qty_strings = [
            format_quantity(line.requirement.quantity_needed) + (f' {line.requirement.unit}' if line.requirement.unit else '')
            for line in lines
        ]
        qty_width = max([len(q) for q in qty_strings] + [len('Qty')])

        header = f'{"Part":<{name_width}}  {"Qty":<{qty_width}}  Part No.'
        table_rows = [header, '-' * len(header)]
        for line, qty in zip(lines, qty_strings):
            table_rows.append(f'{line.requirement.name:<{name_width}}  {qty:<{qty_width}}  {line.requirement.part_number}')

        body = '\n\n\n\n' + '\n'.join(table_rows) + '\n'
        to = self.vendor.email if self.vendor and self.vendor.email else ''
        subject = f'RFQ — {self.job.title}'
        return (
            f'mailto:{to}'
            f'?subject={quote(subject)}'
            f'&body={quote(body.replace(chr(10), chr(13) + chr(10)))}'
        )


class RfqLine(models.Model):
    """One part requirement included in an Rfq's batch."""

    rfq = models.ForeignKey(Rfq, on_delete=models.CASCADE, related_name='lines')
    requirement = models.ForeignKey(PartRequirement, on_delete=models.CASCADE, related_name='rfq_lines')

    class Meta:
        ordering = ['id']
        unique_together = [('rfq', 'requirement')]

    def __str__(self):
        return f'{self.requirement.name} on {self.rfq}'


# --- Uploaded-file cleanup ----------------------------------------------
#
# Django deletes a row but never the file its FileField points at, so
# without this, removing a photo — or an item, spare, location, repair or
# catalog, whose photos/PDF cascade away with it — leaves the file behind in
# media/ forever. Handled here with signals rather than in the delete views
# so cascades, treebeard subtree deletes, and the admin are all covered too.

# Every model with an uploaded file, and that file's field name.
FILE_FIELDS = {
    ItemPhoto: 'image',
    SparePhoto: 'image',
    LocationPhoto: 'image',
    RepairPhoto: 'image',
    CatalogSource: 'file',
}


def _delete_file_on_commit(field_file):
    """Delete a stored file once the surrounding transaction commits — so a
    delete that fails or rolls back never loses the file its row still needs.
    Skips files another row still points at, and removes the per-object
    folder (e.g. items/42/) once it's empty."""
    name = field_file.name
    if not name:
        return
    storage = field_file.storage

    def delete():
        still_used = any(
            model.objects.filter(**{field: name}).exists() for model, field in FILE_FIELDS.items()
        )
        if still_used:
            return
        storage.delete(name)
        folder = os.path.dirname(name)
        if not folder:
            return
        try:
            dirs, files = storage.listdir(folder)
            if not dirs and not files:
                os.rmdir(storage.path(folder))
        except (OSError, NotImplementedError):
            pass

    transaction.on_commit(delete)


def _delete_file_with_row(sender, instance, **kwargs):
    _delete_file_on_commit(getattr(instance, FILE_FIELDS[sender]))


def _delete_replaced_file(sender, instance, **kwargs):
    """When an existing row's file is swapped for a new upload (e.g. a
    catalog's PDF replaced on its edit form), delete the old file."""
    if instance._state.adding or not instance.pk:
        return
    field = FILE_FIELDS[sender]
    old = sender.objects.filter(pk=instance.pk).values_list(field, flat=True).first()
    if old and old != getattr(instance, field).name:
        _delete_file_on_commit(getattr(sender(**{field: old}), field))


for _model in FILE_FIELDS:
    post_delete.connect(_delete_file_with_row, sender=_model, dispatch_uid=f'delete_file_{_model.__name__}')
    pre_save.connect(_delete_replaced_file, sender=_model, dispatch_uid=f'replace_file_{_model.__name__}')
