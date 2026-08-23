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
        fields = ['name', 'description']


class LocationEditForm(BootstrapFormMixin, forms.ModelForm):
    """Used to edit an existing location in place (position in the tree is unchanged;
    use the admin's drag-and-drop tree view to move a location)."""

    class Meta:
        model = Location
        fields = ['name', 'description']


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

    class Meta:
        model = InventoryItem
        fields = ['name', 'category', 'location', 'quantity', 'value', 'condition', 'notes']


ATTACHMENT_FILE_INPUT = forms.ClearableFileInput(attrs={'accept': 'image/*,.pdf'})


class ItemPhotoForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ItemPhoto
        fields = ['image', 'caption', 'is_primary', 'is_receipt']
        widgets = {'image': ATTACHMENT_FILE_INPUT}


class LocationPhotoForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = LocationPhoto
        fields = ['image', 'caption', 'is_primary']
        widgets = {'image': ATTACHMENT_FILE_INPUT}


class ItemStockChoiceField(forms.ModelChoiceField):
    """Shows how much is in stock and where, right in the dropdown, so it's
    obvious what's available to consume in a repair."""

    def label_from_instance(self, obj):
        return f'{obj.name} — {obj.quantity} in stock ({obj.location})'


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
    item = ItemStockChoiceField(queryset=InventoryItem.objects.select_related('location').order_by('name'))

    class Meta:
        model = RepairConsumedItem
        fields = ['item', 'quantity']

    def clean(self):
        cleaned_data = super().clean()
        item = cleaned_data.get('item')
        quantity = cleaned_data.get('quantity')
        if item and quantity and quantity > item.quantity:
            raise forms.ValidationError(
                f'Only {item.quantity} of "{item.name}" in stock — cannot consume {quantity}.'
            )
        return cleaned_data
