from django.contrib import admin
from treebeard.admin import TreeAdmin
from treebeard.forms import movenodeform_factory

from .models import (
    CatalogPart,
    CatalogSection,
    CatalogSource,
    InventoryItem,
    ItemCategory,
    ItemPhoto,
    Job,
    Location,
    LocationHotspot,
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
    Unit,
    Vendor,
)


class LocationPhotoInline(admin.TabularInline):
    model = LocationPhoto
    extra = 1


@admin.register(Location)
class LocationAdmin(TreeAdmin):
    form = movenodeform_factory(Location)
    list_display = ('name', 'value')
    search_fields = ('name',)
    inlines = [LocationPhotoInline]


@admin.register(ItemCategory)
class ItemCategoryAdmin(TreeAdmin):
    form = movenodeform_factory(ItemCategory)
    list_display = ('name',)
    search_fields = ('name',)


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


class ItemPhotoInline(admin.TabularInline):
    model = ItemPhoto
    extra = 1


class SpareInline(admin.TabularInline):
    model = Spare
    extra = 1


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'location', 'quantity', 'unit', 'unit_price', 'total_value', 'condition', 'date_updated')
    list_filter = ('condition', 'category', 'location')
    search_fields = ('name', 'notes')
    inlines = [ItemPhotoInline, SpareInline]

    @admin.display(description='Total value')
    def total_value(self, obj):
        return obj.total_value


class SparePhotoInline(admin.TabularInline):
    model = SparePhoto
    extra = 1


@admin.register(Spare)
class SpareAdmin(admin.ModelAdmin):
    list_display = ('name', 'item', 'location', 'quantity', 'unit', 'unit_price', 'total_value', 'date_updated')
    list_filter = ('location',)
    search_fields = ('name', 'notes', 'item__name')
    inlines = [SparePhotoInline]

    @admin.display(description='Total value')
    def total_value(self, obj):
        return obj.total_value


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


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact_name', 'phone', 'email', 'url')
    search_fields = ('name', 'contact_name', 'email')


class ProposalOptionInline(admin.TabularInline):
    model = ProposalOption
    extra = 1


@admin.register(PartRequirement)
class PartRequirementAdmin(admin.ModelAdmin):
    list_display = ('name', 'part_number', 'job', 'category', 'quantity_needed', 'unit')
    list_filter = ('job', 'category')
    search_fields = ('name', 'part_number', 'notes')
    inlines = [ProposalOptionInline]


class PartRequirementInline(admin.TabularInline):
    model = PartRequirement
    extra = 1
    show_change_link = True


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'location', 'target_date', 'ordered_cost')
    list_filter = ('status',)
    search_fields = ('title', 'description')
    inlines = [PartRequirementInline]

    @admin.display(description='Ordered cost')
    def ordered_cost(self, obj):
        return obj.ordered_cost


@admin.register(ProposalOption)
class ProposalOptionAdmin(admin.ModelAdmin):
    list_display = ('requirement', 'vendor', 'status', 'price', 'unit_price', 'order_number', 'ordered_at')
    list_filter = ('status', 'vendor')
    search_fields = ('requirement__name', 'order_number', 'notes')

    @admin.display(description='Unit price')
    def unit_price(self, obj):
        return obj.unit_price


@admin.register(CatalogSource)
class CatalogSourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'manufacturer', 'model_code', 'created_at')
    search_fields = ('name', 'manufacturer', 'model_code')


class CatalogPartInline(admin.TabularInline):
    model = CatalogPart
    extra = 1


@admin.register(CatalogSection)
class CatalogSectionAdmin(TreeAdmin):
    form = movenodeform_factory(CatalogSection)
    list_display = ('name', 'catalog', 'fig_number', 'page_number')
    list_filter = ('catalog',)
    search_fields = ('name', 'fig_number')
    inlines = [CatalogPartInline]


@admin.register(CatalogPart)
class CatalogPartAdmin(admin.ModelAdmin):
    list_display = ('part_number', 'description', 'section', 'item_no', 'bom_level', 'remarks')
    list_filter = ('section__catalog',)
    search_fields = ('part_number', 'description', 'item_no')


class RfqLineInline(admin.TabularInline):
    model = RfqLine
    extra = 0


@admin.register(Rfq)
class RfqAdmin(admin.ModelAdmin):
    list_display = ('job', 'vendor', 'status', 'created_at', 'sent_at')
    list_filter = ('status', 'vendor')
    search_fields = ('job__title',)
    inlines = [RfqLineInline]
