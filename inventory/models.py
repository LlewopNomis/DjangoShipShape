from decimal import Decimal

from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from treebeard.mp_tree import MP_Node

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
