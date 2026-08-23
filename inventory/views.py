from django.contrib import messages
from django.db import transaction
from django.db.models import ProtectedError, Q, Sum
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
)


class HomeView(TemplateView):
    template_name = 'inventory/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['location_count'] = Location.objects.count()
        context['category_count'] = ItemCategory.objects.count()
        context['item_count'] = InventoryItem.objects.count()
        context['total_value'] = InventoryItem.objects.aggregate(total=Sum('value'))['total']
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
        context['items'] = self.object.items.select_related('category').prefetch_related('photos')
        context['photos'] = self.object.photos.all()
        context['photo_form'] = LocationPhotoForm()
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
        data = {'name': form.cleaned_data['name'], 'description': form.cleaned_data['description']}
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
        context['items'] = self.object.items.select_related('location')
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
        qs = InventoryItem.objects.select_related('category', 'location')
        query = self.request.GET.get('q')
        if query:
            qs = qs.filter(Q(name__icontains=query) | Q(notes__icontains=query))
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
        return qs.order_by('name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['query'] = self.request.GET.get('q', '')
        context['categories'] = ItemCategory.get_tree()
        context['locations'] = Location.get_tree()
        context['selected_category'] = self.request.GET.get('category', '')
        context['selected_location'] = self.request.GET.get('location', '')
        context['filtered_total_value'] = self.get_queryset().aggregate(total=Sum('value'))['total']
        return context


class InventoryItemDetailView(DetailView):
    model = InventoryItem
    template_name = 'inventory/item_detail.html'
    context_object_name = 'item'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['photos'] = self.object.photos.all()
        context['photo_form'] = ItemPhotoForm()
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
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['categories'] = RepairCategory.objects.all()
        context['selected_category'] = self.request.GET.get('category', '')
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
                    form.add_error(None, f'Only {item.quantity} of "{item.name}" in stock.')
                else:
                    item.quantity -= consumption.quantity
                    item.save(update_fields=['quantity'])
                    consumption.save()
                    messages.success(request, f'Recorded {consumption.quantity} x {item.name} used on this repair.')
        if not form.is_valid():
            for error in form.non_field_errors():
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
