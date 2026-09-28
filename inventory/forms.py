from django import forms

from .models import (
    CatalogPart,
    CatalogSource,
    CatalogVariant,
    Equipment,
    InventoryItem,
    ItemCategory,
    ItemPhoto,
    Job,
    Location,
    LocationPhoto,
    PartRequirement,
    ProposalOption,
    Repair,
    RepairCategory,
    RepairConsumedItem,
    RepairPhoto,
    Spare,
    SparePhoto,
    Unit,
    Vendor,
    format_quantity,
)


class BootstrapFormMixin:
    """Adds Bootstrap 5 CSS classes to every field's widget without needing
    a template-tag library like django-widget-tweaks or crispy-forms."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault('class', 'form-check-input')
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs.setdefault('class', 'form-select')
            else:
                widget.attrs.setdefault('class', 'form-control')


class IndentedModelChoiceField(forms.ModelChoiceField):
    """Renders tree nodes in a <select> indented by depth, e.g. '—— Under sole panel 3'."""

    def label_from_instance(self, obj):
        return ('—— ' * (obj.get_depth() - 1)) + obj.name


class LocationForm(BootstrapFormMixin, forms.ModelForm):
    """Used to create a new location. 'parent' controls where it lands in the tree."""

    parent = IndentedModelChoiceField(
        queryset=Location.objects.all(),
        required=False,
        help_text='Leave blank to create a top-level location.',
    )

    class Meta:
        model = Location
        fields = ['name', 'description', 'value']


class LocationEditForm(BootstrapFormMixin, forms.ModelForm):
    """Used to edit an existing location in place (position in the tree is unchanged;
    use the admin's drag-and-drop tree view to move a location)."""

    class Meta:
        model = Location
        fields = ['name', 'description', 'value']


class ItemCategoryForm(BootstrapFormMixin, forms.ModelForm):
    parent = IndentedModelChoiceField(
        queryset=ItemCategory.objects.all(),
        required=False,
        help_text='Leave blank to create a top-level category.',
    )

    class Meta:
        model = ItemCategory
        fields = ['name', 'description']


class ItemCategoryEditForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ItemCategory
        fields = ['name', 'description']


class CatalogPartChoiceField(forms.ModelChoiceField):
    """Shows the part number, description, fig, item No., remark flag and
    catalog, right in the dropdown — a catalog part number alone isn't enough
    to place it. The picker matches on this label, so it has to tell apart
    lines that repeat a part number within one fig (e.g. No.14 vs a
    discontinued No.14-1 flagged 'Z')."""

    def label_from_instance(self, obj):
        section = obj.section
        fig = f'Fig.{section.fig_number} {section.name}' if section.fig_number else section.name
        # No. and remark straight after the description: they're what tells
        # repeated lines apart, and the rest gets cut off in a narrow box.
        parts = [
            f'{obj.part_number} — {obj.description}' if obj.description else obj.part_number,
            f'No.{obj.item_no}' if obj.item_no else '',
            obj.remarks,
            fig,
            section.catalog.name,
        ]
        return ' · '.join(p for p in parts if p)


def catalog_part_queryset():
    # id follows the manual's own line order, so No.14 lists before No.14-1.
    return CatalogPart.objects.select_related('section', 'section__catalog').order_by('part_number', 'id')


class InventoryItemForm(BootstrapFormMixin, forms.ModelForm):
    category = IndentedModelChoiceField(
        queryset=ItemCategory.objects.all(), required=False,
    )
    location = IndentedModelChoiceField(
        queryset=Location.objects.all(), required=True,
    )
    unit = forms.ModelChoiceField(queryset=Unit.objects.all(), required=False)
    catalog_part = CatalogPartChoiceField(queryset=catalog_part_queryset(), required=False, widget=forms.HiddenInput())

    class Meta:
        model = InventoryItem
        fields = ['name', 'category', 'location', 'quantity', 'unit', 'unit_price', 'condition', 'catalog_part', 'notes']


class SpareForm(BootstrapFormMixin, forms.ModelForm):
    location = IndentedModelChoiceField(queryset=Location.objects.all(), required=True)
    unit = forms.ModelChoiceField(queryset=Unit.objects.all(), required=False)

    class Meta:
        model = Spare
        fields = ['name', 'location', 'quantity', 'unit', 'unit_price', 'notes']

    def clean_unit_price(self):
        return self.cleaned_data.get('unit_price') or 0


ATTACHMENT_FILE_INPUT = forms.ClearableFileInput(attrs={'accept': 'image/*,.pdf'})


class SparePhotoForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = SparePhoto
        fields = ['image', 'caption', 'is_primary', 'is_receipt']
        widgets = {'image': ATTACHMENT_FILE_INPUT}


class ItemPhotoForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ItemPhoto
        fields = ['image', 'caption', 'is_primary', 'is_receipt']
        widgets = {'image': ATTACHMENT_FILE_INPUT}


class LocationPhotoForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = LocationPhoto
        fields = ['image', 'caption', 'is_primary', 'is_receipt']
        widgets = {'image': ATTACHMENT_FILE_INPUT}


def stock_item_label(item):
    return f'{item.name} — {format_quantity(item.quantity)} in stock ({item.location})'


class ItemStockChoiceField(forms.ModelChoiceField):
    """Shows how much is in stock and where, right in the dropdown, so it's
    obvious what's available to consume in a repair."""

    def label_from_instance(self, obj):
        return stock_item_label(obj)


class RepairCategoryForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = RepairCategory
        fields = ['name', 'description']


class RepairForm(BootstrapFormMixin, forms.ModelForm):
    location = IndentedModelChoiceField(queryset=Location.objects.all(), required=False)

    class Meta:
        model = Repair
        fields = ['title', 'category', 'location', 'date', 'hours_spent', 'notes']
        widgets = {'date': forms.DateInput(attrs={'type': 'date'})}


class RepairPhotoForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = RepairPhoto
        fields = ['image', 'caption']
        widgets = {'image': ATTACHMENT_FILE_INPUT}


class RepairConsumedItemForm(BootstrapFormMixin, forms.ModelForm):
    """'item' is driven by a type-to-search box in the template (see
    stock_items/json_script in RepairDetailView) rather than a long <select> —
    with 100+ inventory items a plain dropdown is unusable. The field itself
    stays a real ModelChoiceField so an unresolved/tampered value is still
    rejected by validation; only its widget is hidden."""

    item = ItemStockChoiceField(
        queryset=InventoryItem.objects.select_related('location').order_by('name'),
        widget=forms.HiddenInput(),
        error_messages={
            'invalid_choice': 'Pick an item from the search list.',
            'required': 'Type to search, then pick an item from the list.',
        },
    )

    class Meta:
        model = RepairConsumedItem
        fields = ['item', 'quantity']

    def clean(self):
        cleaned_data = super().clean()
        item = cleaned_data.get('item')
        quantity = cleaned_data.get('quantity')
        if item and quantity and quantity > item.quantity:
            raise forms.ValidationError(
                f'Only {format_quantity(item.quantity)} of "{item.name}" in stock — '
                f'cannot consume {format_quantity(quantity)}.'
            )
        return cleaned_data


class CatalogVariantChoiceField(forms.ModelChoiceField):
    """Names the catalog too — 'A' alone means nothing outside its catalog."""

    def label_from_instance(self, obj):
        return f'{obj.catalog.name} — {obj.name} ({obj.code})'


class EquipmentForm(BootstrapFormMixin, forms.ModelForm):
    location = IndentedModelChoiceField(queryset=Location.objects.all(), required=False)
    variant = CatalogVariantChoiceField(
        queryset=CatalogVariant.objects.select_related('catalog'), required=False,
        help_text=Equipment._meta.get_field('variant').help_text,
    )

    class Meta:
        model = Equipment
        fields = ['name', 'location', 'catalog', 'variant', 'serial', 'notes']


class VendorForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Vendor
        fields = ['name', 'contact_name', 'phone', 'email', 'address', 'url', 'notes']


class JobForm(BootstrapFormMixin, forms.ModelForm):
    location = IndentedModelChoiceField(queryset=Location.objects.all(), required=False)

    class Meta:
        model = Job
        fields = ['title', 'description', 'location', 'equipment', 'status', 'target_date']
        widgets = {'target_date': forms.DateInput(attrs={'type': 'date'})}


class PartRequirementForm(BootstrapFormMixin, forms.ModelForm):
    category = IndentedModelChoiceField(queryset=ItemCategory.objects.all(), required=False)
    unit = forms.ModelChoiceField(queryset=Unit.objects.all(), required=False)
    catalog_part = CatalogPartChoiceField(queryset=catalog_part_queryset(), required=False, widget=forms.HiddenInput())
    rfq_vendor = forms.ModelChoiceField(
        queryset=Vendor.objects.all(), required=False, label='RFQ vendor',
        help_text='Vendor to request a quote from for this part.',
    )

    class Meta:
        model = PartRequirement
        fields = ['name', 'part_number', 'category', 'quantity_needed', 'unit', 'catalog_part', 'rfq_vendor', 'notes']


class ProposalOptionForm(BootstrapFormMixin, forms.ModelForm):
    vendor = forms.ModelChoiceField(queryset=Vendor.objects.all(), required=False)
    url = forms.URLField(required=False, label='URL')

    class Meta:
        model = ProposalOption
        fields = ['vendor', 'url', 'price', 'quantity_per_purchase', 'notes']


class ProposalOptionOrderForm(BootstrapFormMixin, forms.ModelForm):
    """Confirms a proposal option as ordered: order number plus a price/quantity
    that can be adjusted from what was originally listed, and the location the
    stock should land in (typically wherever 'ordered but not yet arrived' stock
    is kept, e.g. an 'En Route' location)."""

    location = IndentedModelChoiceField(queryset=Location.objects.all(), required=True)

    class Meta:
        model = ProposalOption
        fields = ['order_number', 'ordered_price', 'ordered_quantity']

    def clean(self):
        cleaned_data = super().clean()
        price = cleaned_data.get('ordered_price')
        quantity = cleaned_data.get('ordered_quantity')
        if price is not None and price < 0:
            self.add_error('ordered_price', 'Price cannot be negative.')
        if quantity is not None and quantity <= 0:
            self.add_error('ordered_quantity', 'Quantity must be greater than zero.')
        return cleaned_data


class CatalogSourceForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = CatalogSource
        fields = ['name', 'manufacturer', 'model_code', 'file', 'notes']
        widgets = {'file': forms.ClearableFileInput(attrs={'accept': '.pdf'})}
