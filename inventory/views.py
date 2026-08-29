from django.contrib import messages
from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, ProtectedError, Q, Sum
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView

from .forms import (
    InventoryItemForm,
    ItemCategoryEditForm,
    ItemCategoryForm,
    ItemPhotoForm,
    LocationEditForm,
    LocationForm,
    LocationPhotoForm,
    RepairCategoryForm,
    RepairConsumedItemForm,
    RepairForm,
    RepairPhotoForm,
    SpareForm,
    SparePhotoForm,
    stock_item_label,
)
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
    format_quantity,
)


def total_value_expr():
    """quantity * unit_price as a queryset expression, for aggregating known total value."""
    return ExpressionWrapper(F('quantity') * F('unit_price'), output_field=DecimalField(max_digits=12, decimal_places=2))


# Sortable columns on the search page. Values are either a field lookup string
# (passed straight to order_by) or an expression exposing .asc()/.desc() (for
# 'total', which isn't a real column — it's quantity x unit_price).
SEARCH_SORT_FIELDS = {
    'name': 'name',
    'category': 'category__name',
    'location': 'location__name',
    'quantity': 'quantity',
    'unit': 'unit__name',
    'unit_price': 'unit_price',
    'total': total_value_expr(),
    'condition': 'condition',
}


# Sortable columns on the repair log page.
REPAIR_SORT_FIELDS = {
    'date': 'date',
    'title': 'title',
    'category': 'category__name',
    'location': 'location__name',
    'hours': 'hours_spent',
    # A plain field-name lookup, same as the others — but only safe because
    # RepairListView.get_queryset() annotates 'cost' (a Sum) before sorting.
    # Sorting by the raw per-row repair_cost_expr() instead would join across
    # the to-many consumed_items relation unaggregated and duplicate rows.
    'cost': 'cost',
}


def repair_cost_expr():
    """Cost of a repair's consumed parts (consumption.quantity x item.unit_price),
    summed across its RepairConsumedItem rows via Sum(repair_cost_expr()) in
    an annotate() — never order_by this raw expression directly (see
    REPAIR_SORT_FIELDS['cost'])."""
    return ExpressionWrapper(
        F('consumed_items__quantity') * F('consumed_items__item__unit_price'),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )


def apply_sort(queryset, sort, sort_fields, default='name'):
    """Order a queryset by GET param 'sort' (e.g. 'name' or '-name'), validated
    against a field-name -> lookup/expression whitelist. Falls back to `default`
    ascending for an empty or unrecognised value."""
    field = sort.lstrip('-')
    descending = sort.startswith('-')
    order_source = sort_fields.get(field)
    if order_source is None:
        return queryset.order_by(default)
    if isinstance(order_source, str):
        return queryset.order_by(f'-{order_source}' if descending else order_source)
    return queryset.order_by(order_source.desc() if descending else order_source.asc())


def _multiword_filter(queryset, query, field_lookups):
    """AND together whitespace-separated terms, each matching any of the given lookups."""
    for term in query.split():
        term_q = Q()
        for lookup in field_lookups:
            term_q |= Q(**{lookup: term})
        queryset = queryset.filter(term_q)
    return queryset


def search_items(queryset, query):
    """Filter an InventoryItem queryset with a loose, multi-word search.

    Each whitespace-separated term must match somewhere (name, notes,
    category or location) but terms can match different fields and in any
    order, so "thread insert" finds a "Threaded Insert" item filed under a
    "Fasteners" category without the user needing the exact name/order.
    """
    return _multiword_filter(
        queryset, query,
        ['name__icontains', 'notes__icontains', 'category__name__icontains', 'location__name__icontains'],
    )


def search_item_text(queryset, query):
    """Filter an InventoryItem queryset by a loose, multi-word match on name/notes only."""
    return _multiword_filter(queryset, query, ['name__icontains', 'notes__icontains'])


def tree_search_ids(model, query):
    """Wildcard-match a Location/ItemCategory tree by name and return the pks of every
    matching node plus its descendants, so e.g. searching "Galley" also picks up items
    filed under "Galley > Under Sink"."""
    matched = _multiword_filter(model.objects.all(), query, ['name__icontains'])
    ids = set()
    for node in matched:
        ids.update(model.get_tree(node).values_list('pk', flat=True))
    return ids


class HomeView(TemplateView):
    template_name = 'inventory/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['location_count'] = Location.objects.count()
        context['category_count'] = ItemCategory.objects.count()
        context['item_count'] = InventoryItem.objects.count()
        context['total_value'] = InventoryItem.objects.aggregate(total=Sum(total_value_expr()))['total']
        context['recent_items'] = InventoryItem.objects.order_by('-date_added')[:8]
        context['recent_repairs'] = Repair.objects.select_related('category', 'location')[:8]
        return context


# --- Locations -------------------------------------------------------------

class LocationListView(ListView):
    model = Location
    template_name = 'inventory/location_list.html'
    context_object_name = 'locations'

    def get_queryset(self):
        return Location.get_tree()


class LocationDetailView(DetailView):
    model = Location
    template_name = 'inventory/location_detail.html'
    context_object_name = 'location'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['children'] = self.object.get_children()
        context['ancestors'] = self.object.get_ancestors()
        items = self.object.items.select_related('category', 'unit').prefetch_related('photos')
        query = self.request.GET.get('q', '')
        if query:
            items = search_items(items, query)
        items = apply_sort(items, self.request.GET.get('sort', ''), SEARCH_SORT_FIELDS)
        context['items'] = items
        context['query'] = query
        context['items_total_value'] = items.aggregate(total=Sum(total_value_expr()))['total']
        context['photos'] = self.object.photos.all()
        context['photo_form'] = LocationPhotoForm()
        context['spares'] = self.object.spares.select_related('item', 'unit')
        return context


class LocationCreateView(CreateView):
    model = Location
    form_class = LocationForm
    template_name = 'inventory/location_form.html'

    def get_initial(self):
        initial = super().get_initial()
        parent_id = self.request.GET.get('parent')
        if parent_id:
            initial['parent'] = parent_id
        return initial

    def form_valid(self, form):
        parent = form.cleaned_data['parent']
        data = {
            'name': form.cleaned_data['name'],
            'description': form.cleaned_data['description'],
            'value': form.cleaned_data['value'],
        }
        self.object = parent.add_child(**data) if parent else Location.add_root(**data)
        messages.success(self.request, f'Location "{self.object.name}" created.')
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('inventory:location_detail', args=[self.object.pk])


class LocationUpdateView(UpdateView):
    model = Location
    form_class = LocationEditForm
    template_name = 'inventory/location_form.html'

    def get_success_url(self):
        return reverse('inventory:location_detail', args=[self.object.pk])


class LocationDeleteView(DeleteView):
    model = Location
    template_name = 'inventory/location_confirm_delete.html'
    success_url = reverse_lazy('inventory:location_list')

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except ProtectedError:
            messages.error(
                self.request,
                'Cannot delete this location: it still has items stored against it '
                '(or against a location beneath it). Move or remove those items first.',
            )
            return redirect('inventory:location_detail', pk=self.object.pk)


def location_photo_add(request, pk):
    location = get_object_or_404(Location, pk=pk)
    if request.method == 'POST':
        form = LocationPhotoForm(request.POST, request.FILES)
        if form.is_valid():
            photo = form.save(commit=False)
            photo.location = location
            photo.save()
            messages.success(request, 'Photo added.')
        else:
            for error in form.errors.get('image', []):
                messages.error(request, error)
    return redirect('inventory:location_detail', pk=location.pk)


def location_photo_delete(request, pk):
    photo = get_object_or_404(LocationPhoto, pk=pk)
    location_pk = photo.location_id
    if request.method == 'POST':
        photo.delete()
        messages.success(request, 'Photo removed.')
    return redirect('inventory:location_detail', pk=location_pk)


# --- Item categories ---------------------------------------------------

class ItemCategoryListView(ListView):
    model = ItemCategory
    template_name = 'inventory/category_list.html'
    context_object_name = 'categories'

    def get_queryset(self):
        return ItemCategory.get_tree()


class ItemCategoryDetailView(DetailView):
    model = ItemCategory
    template_name = 'inventory/category_detail.html'
    context_object_name = 'category'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['children'] = self.object.get_children()
        context['ancestors'] = self.object.get_ancestors()
        items = self.object.items.select_related('location', 'unit').prefetch_related('photos')
        query = self.request.GET.get('q', '')
        if query:
            items = search_items(items, query)
        items = apply_sort(items, self.request.GET.get('sort', ''), SEARCH_SORT_FIELDS)
        context['items'] = items
        context['query'] = query
        context['items_total_value'] = items.aggregate(total=Sum(total_value_expr()))['total']
        return context


class ItemCategoryCreateView(CreateView):
    model = ItemCategory
    form_class = ItemCategoryForm
    template_name = 'inventory/category_form.html'

    def get_initial(self):
        initial = super().get_initial()
        parent_id = self.request.GET.get('parent')
        if parent_id:
            initial['parent'] = parent_id
        return initial

    def form_valid(self, form):
        parent = form.cleaned_data['parent']
        data = {'name': form.cleaned_data['name'], 'description': form.cleaned_data['description']}
        self.object = parent.add_child(**data) if parent else ItemCategory.add_root(**data)
        messages.success(self.request, f'Category "{self.object.name}" created.')
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('inventory:category_detail', args=[self.object.pk])


class ItemCategoryUpdateView(UpdateView):
    model = ItemCategory
    form_class = ItemCategoryEditForm
    template_name = 'inventory/category_form.html'

    def get_success_url(self):
        return reverse('inventory:category_detail', args=[self.object.pk])


class ItemCategoryDeleteView(DeleteView):
    model = ItemCategory
    template_name = 'inventory/category_confirm_delete.html'
    success_url = reverse_lazy('inventory:category_list')

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except ProtectedError:
            messages.error(
                self.request,
                'Cannot delete this category: it still has items assigned to it '
                '(or to a category beneath it). Reassign those items first.',
            )
            return redirect('inventory:category_detail', pk=self.object.pk)


# --- Inventory items -----------------------------------------------------

class InventoryItemListView(ListView):
    model = InventoryItem
    template_name = 'inventory/item_list.html'
    context_object_name = 'items'
    paginate_by = 50

    def get_queryset(self):
        qs = InventoryItem.objects.select_related('category', 'location', 'unit').prefetch_related('photos')
        query = self.request.GET.get('q')
        if query:
            qs = search_items(qs, query)
        category_id = self.request.GET.get('category')
        if category_id:
            category = get_object_or_404(ItemCategory, pk=category_id)
            descendants = ItemCategory.get_tree(category)
            qs = qs.filter(category__in=descendants)
        location_id = self.request.GET.get('location')
        if location_id:
            location = get_object_or_404(Location, pk=location_id)
            descendants = Location.get_tree(location)
            qs = qs.filter(location__in=descendants)
        return apply_sort(qs, self.request.GET.get('sort', ''), SEARCH_SORT_FIELDS)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['query'] = self.request.GET.get('q', '')
        context['categories'] = ItemCategory.get_tree()
        context['locations'] = Location.get_tree()
        context['selected_category'] = self.request.GET.get('category', '')
        context['selected_location'] = self.request.GET.get('location', '')
        context['sort'] = self.request.GET.get('sort', '')
        context['filtered_total_value'] = self.get_queryset().aggregate(total=Sum(total_value_expr()))['total']
        return context


class InventorySearchView(ListView):
    """One page, three independent wildcard filters (item text, location, category).
    With nothing entered it just lists every item."""

    model = InventoryItem
    template_name = 'inventory/search.html'
    context_object_name = 'items'
    paginate_by = 50

    def get_queryset(self):
        qs = InventoryItem.objects.select_related('category', 'location', 'unit').prefetch_related('photos')
        item_q = self.request.GET.get('item_q', '').strip()
        if item_q:
            qs = search_item_text(qs, item_q)
        location_q = self.request.GET.get('location_q', '').strip()
        if location_q:
            qs = qs.filter(location_id__in=tree_search_ids(Location, location_q))
        category_q = self.request.GET.get('category_q', '').strip()
        if category_q:
            qs = qs.filter(category_id__in=tree_search_ids(ItemCategory, category_q))
        return apply_sort(qs, self.request.GET.get('sort', ''), SEARCH_SORT_FIELDS)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['item_q'] = self.request.GET.get('item_q', '')
        context['location_q'] = self.request.GET.get('location_q', '')
        context['sort'] = self.request.GET.get('sort', '')
        context['category_q'] = self.request.GET.get('category_q', '')
        context['filtered_total_value'] = self.get_queryset().aggregate(total=Sum(total_value_expr()))['total']
        context['item_name_options'] = InventoryItem.objects.order_by('name').values_list('name', flat=True).distinct()
        context['location_options'] = Location.get_tree()
        context['category_options'] = ItemCategory.get_tree()
        return context


class InventoryItemDetailView(DetailView):
    model = InventoryItem
    template_name = 'inventory/item_detail.html'
    context_object_name = 'item'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['photos'] = self.object.photos.all()
        context['photo_form'] = ItemPhotoForm()
        context['spares'] = self.object.spares.select_related('location', 'unit')
        context['spare_form'] = SpareForm()
        return context


class InventoryItemCreateView(CreateView):
    model = InventoryItem
    form_class = InventoryItemForm
    template_name = 'inventory/item_form.html'

    def get_initial(self):
        initial = super().get_initial()
        location_id = self.request.GET.get('location')
        if location_id:
            initial['location'] = location_id
        return initial

    def get_success_url(self):
        return reverse('inventory:item_detail', args=[self.object.pk])


class InventoryItemUpdateView(UpdateView):
    model = InventoryItem
    form_class = InventoryItemForm
    template_name = 'inventory/item_form.html'

    def get_success_url(self):
        return reverse('inventory:item_detail', args=[self.object.pk])


class InventoryItemDeleteView(DeleteView):
    model = InventoryItem
    template_name = 'inventory/item_confirm_delete.html'
    success_url = reverse_lazy('inventory:item_list')


def item_photo_add(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    if request.method == 'POST':
        form = ItemPhotoForm(request.POST, request.FILES)
        if form.is_valid():
            photo = form.save(commit=False)
            photo.item = item
            photo.save()
            messages.success(request, 'Photo added.')
        else:
            for error in form.errors.get('image', []):
                messages.error(request, error)
    return redirect('inventory:item_detail', pk=item.pk)


def item_photo_delete(request, pk):
    photo = get_object_or_404(ItemPhoto, pk=pk)
    item_pk = photo.item_id
    if request.method == 'POST':
        photo.delete()
        messages.success(request, 'Photo removed.')
    return redirect('inventory:item_detail', pk=item_pk)


def spare_add(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    if request.method == 'POST':
        form = SpareForm(request.POST)
        if form.is_valid():
            spare = form.save(commit=False)
            spare.item = item
            spare.save()
            messages.success(request, f'Spare "{spare.name}" added.')
        else:
            for field_errors in form.errors.values():
                for error in field_errors:
                    messages.error(request, error)
    return redirect('inventory:item_detail', pk=item.pk)


class SpareUpdateView(UpdateView):
    model = Spare
    form_class = SpareForm
    template_name = 'inventory/spare_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['photos'] = self.object.photos.all()
        context['photo_form'] = SparePhotoForm()
        return context

    def get_success_url(self):
        return reverse('inventory:item_detail', args=[self.object.item_id])


def spare_delete(request, pk):
    spare = get_object_or_404(Spare, pk=pk)
    item_pk = spare.item_id
    if request.method == 'POST':
        spare.delete()
        messages.success(request, 'Spare removed.')
    return redirect('inventory:item_detail', pk=item_pk)


def spare_photo_add(request, pk):
    spare = get_object_or_404(Spare, pk=pk)
    if request.method == 'POST':
        form = SparePhotoForm(request.POST, request.FILES)
        if form.is_valid():
            photo = form.save(commit=False)
            photo.spare = spare
            photo.save()
            messages.success(request, 'Photo added.')
        else:
            for error in form.errors.get('image', []):
                messages.error(request, error)
    return redirect('inventory:spare_edit', pk=spare.pk)


def spare_photo_delete(request, pk):
    photo = get_object_or_404(SparePhoto, pk=pk)
    spare_pk = photo.spare_id
    if request.method == 'POST':
        photo.delete()
        messages.success(request, 'Photo removed.')
    return redirect('inventory:spare_edit', pk=spare_pk)


# --- Repair categories ---------------------------------------------------

class RepairCategoryListView(ListView):
    model = RepairCategory
    template_name = 'inventory/repair_category_list.html'
    context_object_name = 'categories'


class RepairCategoryCreateView(CreateView):
    model = RepairCategory
    form_class = RepairCategoryForm
    template_name = 'inventory/repair_category_form.html'
    success_url = reverse_lazy('inventory:repair_category_list')


class RepairCategoryDeleteView(DeleteView):
    model = RepairCategory
    template_name = 'inventory/repair_category_confirm_delete.html'
    success_url = reverse_lazy('inventory:repair_category_list')


# --- Repairs ---------------------------------------------------------------

class RepairListView(ListView):
    model = Repair
    template_name = 'inventory/repair_list.html'
    context_object_name = 'repairs'
    paginate_by = 50

    def get_queryset(self):
        qs = Repair.objects.select_related('category', 'location')
        category_id = self.request.GET.get('category')
        if category_id:
            qs = qs.filter(category_id=category_id)
        qs = qs.annotate(cost=Sum(repair_cost_expr()))
        return apply_sort(qs, self.request.GET.get('sort', ''), REPAIR_SORT_FIELDS, default='-date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['categories'] = RepairCategory.objects.all()
        context['selected_category'] = self.request.GET.get('category', '')
        context['sort'] = self.request.GET.get('sort', '')
        return context


class RepairDetailView(DetailView):
    model = Repair
    template_name = 'inventory/repair_detail.html'
    context_object_name = 'repair'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['consumed_items'] = self.object.consumed_items.select_related('item')
        context['photos'] = self.object.photos.all()
        context['photo_form'] = RepairPhotoForm()
        context['consume_form'] = RepairConsumedItemForm()
        context['stock_items'] = [
            {'id': i.pk, 'label': stock_item_label(i)}
            for i in InventoryItem.objects.select_related('location').order_by('name')
        ]
        return context


class RepairCreateView(CreateView):
    model = Repair
    form_class = RepairForm
    template_name = 'inventory/repair_form.html'

    def get_initial(self):
        initial = super().get_initial()
        location_id = self.request.GET.get('location')
        if location_id:
            initial['location'] = location_id
        return initial

    def get_success_url(self):
        return reverse('inventory:repair_detail', args=[self.object.pk])


class RepairUpdateView(UpdateView):
    model = Repair
    form_class = RepairForm
    template_name = 'inventory/repair_form.html'

    def get_success_url(self):
        return reverse('inventory:repair_detail', args=[self.object.pk])


class RepairDeleteView(DeleteView):
    model = Repair
    template_name = 'inventory/repair_confirm_delete.html'
    success_url = reverse_lazy('inventory:repair_list')

    def form_valid(self, form):
        with transaction.atomic():
            for consumption in self.object.consumed_items.select_related('item'):
                item = InventoryItem.objects.select_for_update().get(pk=consumption.item_id)
                item.quantity += consumption.quantity
                item.save(update_fields=['quantity'])
            return super().form_valid(form)


def repair_photo_add(request, pk):
    repair = get_object_or_404(Repair, pk=pk)
    if request.method == 'POST':
        form = RepairPhotoForm(request.POST, request.FILES)
        if form.is_valid():
            photo = form.save(commit=False)
            photo.repair = repair
            photo.save()
            messages.success(request, 'Photo added.')
        else:
            for error in form.errors.get('image', []):
                messages.error(request, error)
    return redirect('inventory:repair_detail', pk=repair.pk)


def repair_photo_delete(request, pk):
    photo = get_object_or_404(RepairPhoto, pk=pk)
    repair_pk = photo.repair_id
    if request.method == 'POST':
        photo.delete()
        messages.success(request, 'Photo removed.')
    return redirect('inventory:repair_detail', pk=repair_pk)


def repair_consume_item(request, pk):
    repair = get_object_or_404(Repair, pk=pk)
    if request.method == 'POST':
        form = RepairConsumedItemForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                consumption = form.save(commit=False)
                consumption.repair = repair
                item = InventoryItem.objects.select_for_update().get(pk=consumption.item_id)
                if consumption.quantity > item.quantity:
                    form.add_error(None, f'Only {format_quantity(item.quantity)} of "{item.name}" in stock.')
                else:
                    item.quantity -= consumption.quantity
                    item.save(update_fields=['quantity'])
                    consumption.save()
                    messages.success(
                        request,
                        f'Recorded {format_quantity(consumption.quantity)} x {item.name} used on this repair.',
                    )
        if not form.is_valid():
            for field_errors in form.errors.values():
                for error in field_errors:
                    messages.error(request, error)
    return redirect('inventory:repair_detail', pk=repair.pk)


def repair_consumed_item_delete(request, pk):
    consumption = get_object_or_404(RepairConsumedItem, pk=pk)
    repair_pk = consumption.repair_id
    if request.method == 'POST':
        with transaction.atomic():
            item = InventoryItem.objects.select_for_update().get(pk=consumption.item_id)
            item.quantity += consumption.quantity
            item.save(update_fields=['quantity'])
            consumption.delete()
        messages.success(request, 'Consumption removed and quantity restored.')
    return redirect('inventory:repair_detail', pk=repair_pk)
