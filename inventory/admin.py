from django.contrib import admin
from treebeard.admin import TreeAdmin
from treebeard.forms import movenodeform_factory

from .models import (
    InventoryItem,
    ItemCategory,
    ItemPhoto,
    Location,
    LocationHotspot,
    LocationPhoto,
    Repair,
    RepairCategory,
    RepairConsumedItem,
    RepairPhoto,
)


class LocationPhotoInline(admin.TabularInline):
    model = LocationPhoto
    extra = 1


@admin.register(Location)
class LocationAdmin(TreeAdmin):
    form = movenodeform_factory(Location)
    list_display = ('name',)
    search_fields = ('name',)
    inlines = [LocationPhotoInline]


@admin.register(ItemCategory)
class ItemCategoryAdmin(TreeAdmin):
    form = movenodeform_factory(ItemCategory)
    list_display = ('name',)
    search_fields = ('name',)


class ItemPhotoInline(admin.TabularInline):
    model = ItemPhoto
    extra = 1


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'location', 'quantity', 'value', 'condition', 'date_updated')
    list_filter = ('condition', 'category', 'location')
    search_fields = ('name', 'notes')
    inlines = [ItemPhotoInline]


@admin.register(LocationHotspot)
class LocationHotspotAdmin(admin.ModelAdmin):
    list_display = ('photo', 'target_location', 'shape', 'label')


@admin.register(RepairCategory)
class RepairCategoryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


class RepairPhotoInline(admin.TabularInline):
    model = RepairPhoto
    extra = 1


class RepairConsumedItemInline(admin.TabularInline):
    model = RepairConsumedItem
    extra = 1


@admin.register(Repair)
class RepairAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'location', 'date', 'hours_spent')
    list_filter = ('category', 'location')
    search_fields = ('title', 'notes')
    inlines = [RepairConsumedItemInline, RepairPhotoInline]
