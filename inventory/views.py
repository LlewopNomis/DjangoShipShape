import re
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, ProtectedError, Q, Sum
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView

from .forms import (
    CatalogSourceForm,
    InventoryItemForm,
    ItemCategoryEditForm,
    ItemCategoryForm,
    ItemPhotoForm,
    JobForm,
    LocationEditForm,
    LocationForm,
    LocationPhotoForm,
    PartRequirementForm,
    ProposalOptionForm,
    ProposalOptionOrderForm,
    RepairCategoryForm,
    RepairConsumedItemForm,
    RepairForm,
    RepairPhotoForm,
    SpareForm,
    SparePhotoForm,
    VendorForm,
    stock_item_label,
)
from .models import (
    CatalogPart,
    CatalogSection,
    CatalogSource,
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
    Rfq,
    RfqLine,
    Spare,
    SparePhoto,
    Vendor,
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


# Sortable columns on a catalog section's parts table.
CATALOG_PART_SORT_FIELDS = {
    'item_no': 'item_no',
    'part_number': 'part_number',
    'description': 'description',
}


def natural_key(value):
    """Sort key that orders embedded numbers numerically, so catalog item/fig
    numbers like '9', '14-1', '28' sort as a reader expects rather than as
    plain strings ('14-1' < '28' < '9')."""
    return [(0, int(tok), '') if tok.isdigit() else (1, 0, tok.lower())
            for tok in re.findall(r'\d+|\D+', value or '')]


# Sortable columns on a job's requirements table. Sorted in Python (a job
# only has a handful of rows) because item/fig numbers need natural ordering.
# Each maps to a key function; rows where the key is empty always sort last.
REQUIREMENT_SORT_KEYS = {
    'description': lambda r: natural_key(r.name),
    'part_number': lambda r: natural_key(r.part_number),
    'item_no': lambda r: natural_key(r.catalog_part.item_no if r.catalog_part else ''),
    'fig': lambda r: (
        natural_key(r.catalog_part.section.fig_number) + [(2, 0, r.catalog_part.section.name.lower())]
        if r.catalog_part else []
    ),
    'category': lambda r: natural_key(r.category.name if r.category else ''),
    'needed': lambda r: r.quantity_needed,
    'vendor': lambda r: natural_key(r.rfq_vendor.name if r.rfq_vendor else ''),
}
# Used when no ?sort= is given: the manual's own order, figure by figure.
REQUIREMENT_DEFAULT_SORT = 'fig,item_no,part_number'


def sort_requirements(requirements, sort):
    """Sort by a comma-separated list of columns, e.g. 'fig,-part_number' —
    the first is the primary sort, later ones break ties. Done as one stable
    sort per column, least significant first; rows blank in a column always
    go after the non-blank ones for that column, whichever direction."""
    rows = list(requirements)
    terms = [t for t in sort.split(',') if t]
    # Within a fig, fall back to the manual's own item order unless the
    # user has chosen an explicit No. sort of their own.
    if any(t.lstrip('-') == 'fig' for t in terms) and not any(t.lstrip('-') == 'item_no' for t in terms):
        terms.append('item_no')
    for term in reversed(terms):
        key = REQUIREMENT_SORT_KEYS.get(term.lstrip('-'))
        if key is None:
            continue
        present = [r for r in rows if key(r) not in ('', [], None)]
        missing = [r for r in rows if key(r) in ('', [], None)]
        rows = sorted(present, key=key, reverse=term.startswith('-')) + missing
    return rows


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


def attach_subtree_item_counts(nodes, direct_counts):
    """Attach `.item_count` to each Location/ItemCategory node: the number of
    items filed directly on it plus everywhere beneath it in the tree, so a
    branch's count tells you whether it's worth expanding before you go
    digging. `direct_counts` maps node pk -> count of items filed directly
    on that node (e.g. from `.values('location_id').annotate(Count('id'))`).
    `nodes` must be the full tree (every node has its parent present too),
    e.g. from `Model.get_tree()`."""
    nodes = list(nodes)
    totals = {node.path: direct_counts.get(node.pk, 0) for node in nodes}
    for node in sorted(nodes, key=lambda n: -len(n.path)):
        node.item_count = totals[node.path]
        if len(node.path) > node.steplen:
            parent_path = node.path[:-node.steplen]
            totals[parent_path] = totals.get(parent_path, 0) + node.item_count
    return nodes


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
        direct_counts = dict(InventoryItem.objects.values('location_id').annotate(c=Count('id')).values_list('location_id', 'c'))
        return attach_subtree_item_counts(Location.get_tree(), direct_counts)


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
        direct_counts = dict(InventoryItem.objects.values('category_id').annotate(c=Count('id')).values_list('category_id', 'c'))
        return attach_subtree_item_counts(ItemCategory.get_tree(), direct_counts)


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

    def get_queryset(self):
        return InventoryItem.objects.select_related('catalog_part__section__catalog')

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


# --- Vendors -----------------------------------------------------------

class VendorListView(ListView):
    model = Vendor
    template_name = 'inventory/vendor_list.html'
    context_object_name = 'vendors'


class VendorCreateView(CreateView):
    model = Vendor
    form_class = VendorForm
    template_name = 'inventory/vendor_form.html'

    def get_success_url(self):
        return reverse('inventory:vendor_detail', args=[self.object.pk])


class VendorDetailView(DetailView):
    model = Vendor
    template_name = 'inventory/vendor_detail.html'
    context_object_name = 'vendor'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['purchase_history'] = (
            self.object.proposal_options
            .filter(status__in=[ProposalOption.STATUS_ORDERED, ProposalOption.STATUS_RECEIVED])
            .select_related('requirement', 'requirement__job')
            .order_by('-ordered_at')
        )
        return context


class VendorUpdateView(UpdateView):
    model = Vendor
    form_class = VendorForm
    template_name = 'inventory/vendor_form.html'

    def get_success_url(self):
        return reverse('inventory:vendor_detail', args=[self.object.pk])


class VendorDeleteView(DeleteView):
    model = Vendor
    template_name = 'inventory/vendor_confirm_delete.html'
    success_url = reverse_lazy('inventory:vendor_list')

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except ProtectedError:
            messages.error(
                self.request,
                'Cannot delete this vendor: it still has purchase options recorded against it. '
                'Reassign or remove those first.',
            )
            return redirect('inventory:vendor_detail', pk=self.object.pk)


# --- Jobs, part requirements & proposal options -------------------------

class JobListView(ListView):
    model = Job
    template_name = 'inventory/job_list.html'
    context_object_name = 'jobs'

    def get_queryset(self):
        qs = Job.objects.select_related('location')
        status = self.request.GET.get('status')
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = Job.STATUS_CHOICES
        context['selected_status'] = self.request.GET.get('status', '')
        return context


class JobCreateView(CreateView):
    model = Job
    form_class = JobForm
    template_name = 'inventory/job_form.html'

    def get_success_url(self):
        return reverse('inventory:job_detail', args=[self.object.pk])


class JobDetailView(DetailView):
    model = Job
    template_name = 'inventory/job_detail.html'
    context_object_name = 'job'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        requirements = (
            self.object.requirements
            .select_related('category', 'unit', 'rfq_vendor', 'catalog_part__section__catalog')
            .prefetch_related('options')
        )
        vendor_filter = self.request.GET.get('vendor', '')
        if vendor_filter == 'none':
            requirements = requirements.filter(rfq_vendor__isnull=True)
        elif vendor_filter:
            requirements = requirements.filter(rfq_vendor_id=vendor_filter)
        sort = self.request.GET.get('sort', '')
        effective_sort = sort or REQUIREMENT_DEFAULT_SORT
        context['requirements'] = sort_requirements(requirements, effective_sort)
        context['sort'] = sort
        context['effective_sort'] = effective_sort
        context['requirement_form'] = PartRequirementForm()
        context['vendor_filter'] = vendor_filter
        context['assigned_vendors'] = Vendor.objects.filter(
            rfq_requirements__job=self.object,
        ).distinct().order_by('name')
        context['all_vendors'] = Vendor.objects.order_by('name')
        context['rfqs'] = self.object.rfqs.select_related('vendor')
        return context


class JobUpdateView(UpdateView):
    model = Job
    form_class = JobForm
    template_name = 'inventory/job_form.html'

    def get_success_url(self):
        return reverse('inventory:job_detail', args=[self.object.pk])


class JobDeleteView(DeleteView):
    model = Job
    template_name = 'inventory/job_confirm_delete.html'
    success_url = reverse_lazy('inventory:job_list')


def requirement_add(request, pk):
    job = get_object_or_404(Job, pk=pk)
    if request.method == 'POST':
        form = PartRequirementForm(request.POST)
        if form.is_valid():
            requirement = form.save(commit=False)
            requirement.job = job
            requirement.save()
            messages.success(request, f'"{requirement.name}" added to the job.')
        else:
            for field_errors in form.errors.values():
                for error in field_errors:
                    messages.error(request, error)
    return redirect('inventory:job_detail', pk=job.pk)


class RequirementDetailView(DetailView):
    model = PartRequirement
    template_name = 'inventory/requirement_detail.html'
    context_object_name = 'requirement'

    def get_queryset(self):
        return PartRequirement.objects.select_related(
            'job', 'category', 'unit', 'catalog_part', 'catalog_part__section', 'catalog_part__section__catalog',
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['options'] = self.object.options.select_related('vendor')
        context['option_form'] = ProposalOptionForm()
        return context


class RequirementUpdateView(UpdateView):
    model = PartRequirement
    form_class = PartRequirementForm
    template_name = 'inventory/requirement_form.html'

    def get_success_url(self):
        return reverse('inventory:requirement_detail', args=[self.object.pk])


def requirement_delete(request, pk):
    requirement = get_object_or_404(PartRequirement, pk=pk)
    job_pk = requirement.job_id
    if request.method == 'POST':
        requirement.delete()
        messages.success(request, 'Part requirement removed.')
    return redirect('inventory:job_detail', pk=job_pk)


def requirement_set_rfq_vendor(request, pk):
    """Quick inline vendor tag on a job's requirement list — deliberately
    separate from the full edit form so batch-tagging many requirements
    ahead of an RFQ doesn't mean opening each one individually."""
    requirement = get_object_or_404(PartRequirement, pk=pk)
    if request.method == 'POST':
        vendor_id = request.POST.get('rfq_vendor') or None
        requirement.rfq_vendor_id = vendor_id
        requirement.save(update_fields=['rfq_vendor'])
    redirect_url = reverse('inventory:job_detail', args=[requirement.job_id])
    keep = {k: request.POST.get(v, '') for k, v in (('vendor', 'vendor_filter'), ('sort', 'sort'))}
    keep = {k: v for k, v in keep.items() if v}
    if keep:
        redirect_url += f'?{urlencode(keep)}'
    return redirect(redirect_url)


def option_add(request, pk):
    requirement = get_object_or_404(PartRequirement, pk=pk)
    if request.method == 'POST':
        form = ProposalOptionForm(request.POST)
        if form.is_valid():
            option = form.save(commit=False)
            option.requirement = requirement
            option.save()
            messages.success(request, 'Option added for comparison.')
        else:
            for field_errors in form.errors.values():
                for error in field_errors:
                    messages.error(request, error)
    return redirect('inventory:requirement_detail', pk=requirement.pk)


class OptionUpdateView(UpdateView):
    model = ProposalOption
    form_class = ProposalOptionForm
    template_name = 'inventory/option_form.html'

    def get_success_url(self):
        return reverse('inventory:requirement_detail', args=[self.object.requirement_id])


def option_delete(request, pk):
    option = get_object_or_404(ProposalOption, pk=pk)
    requirement_pk = option.requirement_id
    if request.method == 'POST':
        option.delete()
        messages.success(request, 'Option removed.')
    return redirect('inventory:requirement_detail', pk=requirement_pk)


def option_order(request, pk):
    option = get_object_or_404(ProposalOption.objects.select_related('requirement', 'requirement__job'), pk=pk)
    job = option.requirement.job
    if request.method == 'POST':
        form = ProposalOptionOrderForm(request.POST, instance=option)
        if form.is_valid():
            with transaction.atomic():
                ordered_option = form.save(commit=False)
                ordered_option.status = ProposalOption.STATUS_ORDERED
                ordered_option.ordered_at = timezone.now()
                ordered_option.save()
                location = form.cleaned_data['location']
                requirement = ordered_option.requirement
                part_number_note = f' — part #{requirement.part_number}' if requirement.part_number else ''
                InventoryItem.objects.create(
                    name=requirement.name,
                    category=requirement.category,
                    location=location,
                    quantity=ordered_option.ordered_quantity,
                    unit=requirement.unit,
                    unit_price=(ordered_option.ordered_price / ordered_option.ordered_quantity).quantize(Decimal('0.01')),
                    notes=f'Ordered from {ordered_option.vendor or "unlisted vendor"} — order #{ordered_option.order_number or "n/a"}'
                          f'{part_number_note} (job: {job.title}).',
                    sourced_from=ordered_option,
                    catalog_part=requirement.catalog_part,
                )
            messages.success(request, f'Marked as ordered and added to stock in {location}.')
            return redirect('inventory:requirement_detail', pk=requirement.pk)
        return render(request, 'inventory/option_order_form.html', {'option': option, 'form': form, 'job': job})

    default_location = Location.objects.filter(name__iexact='En Route').first()
    form = ProposalOptionOrderForm(instance=option, initial={
        'ordered_price': option.price,
        'ordered_quantity': option.quantity_per_purchase,
        'location': default_location.pk if default_location else None,
    })
    return render(request, 'inventory/option_order_form.html', {'option': option, 'form': form, 'job': job})


# --- Parts catalogue -------------------------------------------------------

class CatalogSourceListView(ListView):
    model = CatalogSource
    template_name = 'inventory/catalog_source_list.html'
    context_object_name = 'catalogs'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get('q', '').strip()
        context['query'] = query
        if query:
            context['part_results'] = (
                CatalogPart.objects.select_related('section', 'section__catalog')
                .filter(Q(part_number__icontains=query) | Q(description__icontains=query))[:100]
            )
        return context


class CatalogSourceCreateView(CreateView):
    model = CatalogSource
    form_class = CatalogSourceForm
    template_name = 'inventory/catalog_source_form.html'

    def get_success_url(self):
        return reverse('inventory:catalog_source_detail', args=[self.object.pk])


class CatalogSourceDetailView(DetailView):
    model = CatalogSource
    template_name = 'inventory/catalog_source_detail.html'
    context_object_name = 'catalog'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['sections'] = CatalogSection.objects.filter(catalog=self.object).order_by('path')
        return context


class CatalogSourceUpdateView(UpdateView):
    model = CatalogSource
    form_class = CatalogSourceForm
    template_name = 'inventory/catalog_source_form.html'

    def get_success_url(self):
        return reverse('inventory:catalog_source_detail', args=[self.object.pk])


class CatalogSourceDeleteView(DeleteView):
    model = CatalogSource
    template_name = 'inventory/catalog_source_confirm_delete.html'
    success_url = reverse_lazy('inventory:catalog_source_list')


class CatalogSectionDetailView(DetailView):
    model = CatalogSection
    template_name = 'inventory/catalog_section_detail.html'
    context_object_name = 'section'

    def get_queryset(self):
        return CatalogSection.objects.select_related('catalog')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sort = self.request.GET.get('sort', '')
        context['parts'] = apply_sort(self.object.parts.all(), sort, CATALOG_PART_SORT_FIELDS, default='id')
        context['sort'] = sort
        context['ancestors'] = self.object.get_ancestors()
        context['children'] = self.object.get_children()
        return context


# --- RFQs --------------------------------------------------------------

def rfq_create(request, pk):
    """Drafts an RFQ from the requirements currently matching a vendor filter
    on the job page — 'All' isn't a valid choice here since mixing several
    vendors' parts into one email wouldn't make sense."""
    job = get_object_or_404(Job, pk=pk)
    if request.method != 'POST':
        return redirect('inventory:job_detail', pk=job.pk)

    vendor_param = request.POST.get('vendor', '')
    vendor = None
    requirements = job.requirements.all()
    if vendor_param == 'none':
        requirements = requirements.filter(rfq_vendor__isnull=True)
    elif vendor_param:
        vendor = get_object_or_404(Vendor, pk=vendor_param)
        requirements = requirements.filter(rfq_vendor=vendor)
    else:
        messages.error(request, 'Filter to a specific vendor (or "no vendor assigned") before drafting an RFQ.')
        return redirect('inventory:job_detail', pk=job.pk)

    if not requirements.exists():
        messages.error(request, 'No part requirements match that filter.')
        return redirect(f"{reverse('inventory:job_detail', args=[job.pk])}?vendor={vendor_param}")

    with transaction.atomic():
        rfq = Rfq.objects.create(job=job, vendor=vendor)
        RfqLine.objects.bulk_create([RfqLine(rfq=rfq, requirement=r) for r in requirements])

    return redirect('inventory:rfq_detail', pk=rfq.pk)


class RfqListView(ListView):
    model = Rfq
    template_name = 'inventory/rfq_list.html'
    context_object_name = 'rfqs'

    def get_queryset(self):
        return Rfq.objects.select_related('job', 'vendor')


class RfqDetailView(DetailView):
    model = Rfq
    template_name = 'inventory/rfq_detail.html'
    context_object_name = 'rfq'

    def get_queryset(self):
        return Rfq.objects.select_related('job', 'vendor')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['lines'] = self.object.lines.select_related('requirement', 'requirement__unit', 'requirement__category')
        return context


def rfq_mark_sent(request, pk):
    rfq = get_object_or_404(Rfq, pk=pk)
    if request.method == 'POST':
        rfq.status = Rfq.STATUS_SENT
        rfq.sent_at = timezone.now()
        rfq.save(update_fields=['status', 'sent_at'])
        messages.success(request, 'Marked as sent.')
    return redirect('inventory:rfq_detail', pk=rfq.pk)


def rfq_mark_draft(request, pk):
    """Undoes 'mark as sent' — for correcting a mistaken click, or for an
    RFQ that was marked sent but, on reflection, wasn't actually sent."""
    rfq = get_object_or_404(Rfq, pk=pk)
    if request.method == 'POST':
        rfq.status = Rfq.STATUS_DRAFT
        rfq.sent_at = None
        rfq.save(update_fields=['status', 'sent_at'])
        messages.success(request, 'Marked as draft.')
    return redirect('inventory:rfq_detail', pk=rfq.pk)


def rfq_delete(request, pk):
    """Only ever deletes a still-draft RFQ — one that's been sent is a real
    record of what went to a vendor and shouldn't disappear by accident."""
    rfq = get_object_or_404(Rfq, pk=pk)
    if request.method == 'POST':
        if rfq.status != Rfq.STATUS_DRAFT:
            messages.error(request, 'Only a draft RFQ can be deleted — mark it back to draft first.')
            return redirect('inventory:rfq_detail', pk=rfq.pk)
        rfq.delete()
        messages.success(request, 'RFQ deleted.')
    return redirect('inventory:rfq_list')
