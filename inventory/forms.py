from django import forms

from .models import (
    InventoryItem,
    ItemCategory,
    ItemPhoto,
    Location,
    LocationPhoto,
    Repair,
    RepairCategory,
    RepairConsumedItem,
    RepairPhoto,
    Spare,
    SparePhoto,
    Unit,
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


class InventoryItemForm(BootstrapFormMixin, forms.ModelForm):
    category = IndentedModelChoiceField(
        queryset=ItemCategory.objects.all(), required=False,
    )
    location = IndentedModelChoiceField(
        queryset=Location.objects.all(), required=True,
    )
    unit = forms.ModelChoiceField(queryset=Unit.objects.all(), required=False)

    class Meta:
        model = InventoryItem
        fields = ['name', 'category', 'location', 'quantity', 'unit', 'unit_price', 'condition', 'notes']


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
