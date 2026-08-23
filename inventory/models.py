from django.db import models
from treebeard.mp_tree import MP_Node


class Location(MP_Node):
    """A place on the boat (e.g. Galley > Floor > Under Panel 3)."""

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    node_order_by = ['name']

    class Meta:
        verbose_name = 'location'
        verbose_name_plural = 'locations'

    def __str__(self):
        return self.name


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
    quantity = models.PositiveIntegerField(default=1)
    condition = models.CharField(
        max_length=20, choices=CONDITION_CHOICES, default=CONDITION_GOOD, blank=True,
    )
    value = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Total value of this line (all units), in $, if known. Useful for insurance/valuation.',
    )
    notes = models.TextField(blank=True)
    date_added = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


def item_photo_path(instance, filename):
    return f'items/{instance.item_id}/{filename}'


def location_photo_path(instance, filename):
    return f'locations/{instance.location_id}/{filename}'


class ItemPhoto(models.Model):
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='photos')
    image = models.ImageField(upload_to=item_photo_path)
    caption = models.CharField(max_length=200, blank=True)
    is_primary = models.BooleanField(default=False)
    is_receipt = models.BooleanField(default=False, help_text='This is a purchase receipt, not a photo of the item.')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_primary', 'uploaded_at']

    def __str__(self):
        return f'Photo of {self.item.name}'


class LocationPhoto(models.Model):
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='photos')
    image = models.ImageField(upload_to=location_photo_path)
    caption = models.CharField(max_length=200, blank=True)
    is_primary = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_primary', 'uploaded_at']

    def __str__(self):
        return f'Photo of {self.location.name}'


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
    image = models.ImageField(upload_to=repair_photo_path)
    caption = models.CharField(max_length=200, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']

    def __str__(self):
        return f'Photo of {self.repair}'


class RepairConsumedItem(models.Model):
    """An inventory item (and quantity) used up in a repair. Consuming an
    item here decrements InventoryItem.quantity; deleting the record
    restores it (handled in the view, not here, so the stock adjustment
    stays visible/auditable rather than hidden in a signal)."""

    repair = models.ForeignKey(Repair, on_delete=models.CASCADE, related_name='consumed_items')
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name='consumptions')
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.quantity} x {self.item.name}'
