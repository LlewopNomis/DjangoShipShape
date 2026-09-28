from django.urls import path

from . import views

app_name = 'inventory'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),

    path('locations/', views.LocationListView.as_view(), name='location_list'),
    path('locations/add/', views.LocationCreateView.as_view(), name='location_add'),
    path('locations/<int:pk>/', views.LocationDetailView.as_view(), name='location_detail'),
    path('locations/<int:pk>/edit/', views.LocationUpdateView.as_view(), name='location_edit'),
    path('locations/<int:pk>/delete/', views.LocationDeleteView.as_view(), name='location_delete'),
    path('locations/<int:pk>/photo/add/', views.location_photo_add, name='location_photo_add'),
    path('locations/photo/<int:pk>/delete/', views.location_photo_delete, name='location_photo_delete'),

    path('categories/', views.ItemCategoryListView.as_view(), name='category_list'),
    path('categories/add/', views.ItemCategoryCreateView.as_view(), name='category_add'),
    path('categories/<int:pk>/', views.ItemCategoryDetailView.as_view(), name='category_detail'),
    path('categories/<int:pk>/edit/', views.ItemCategoryUpdateView.as_view(), name='category_edit'),
    path('categories/<int:pk>/delete/', views.ItemCategoryDeleteView.as_view(), name='category_delete'),

    path('search/', views.InventorySearchView.as_view(), name='search'),

    path('items/', views.InventoryItemListView.as_view(), name='item_list'),
    path('items/add/', views.InventoryItemCreateView.as_view(), name='item_add'),
    path('items/<int:pk>/', views.InventoryItemDetailView.as_view(), name='item_detail'),
    path('items/<int:pk>/edit/', views.InventoryItemUpdateView.as_view(), name='item_edit'),
    path('items/<int:pk>/delete/', views.InventoryItemDeleteView.as_view(), name='item_delete'),
    path('items/<int:pk>/photo/add/', views.item_photo_add, name='item_photo_add'),
    path('items/photo/<int:pk>/delete/', views.item_photo_delete, name='item_photo_delete'),
    path('items/<int:pk>/spares/add/', views.spare_add, name='spare_add'),
    path('spares/<int:pk>/edit/', views.SpareUpdateView.as_view(), name='spare_edit'),
    path('spares/<int:pk>/delete/', views.spare_delete, name='spare_delete'),
    path('spares/<int:pk>/photo/add/', views.spare_photo_add, name='spare_photo_add'),
    path('spares/photo/<int:pk>/delete/', views.spare_photo_delete, name='spare_photo_delete'),

    path('repair-categories/', views.RepairCategoryListView.as_view(), name='repair_category_list'),
    path('repair-categories/add/', views.RepairCategoryCreateView.as_view(), name='repair_category_add'),
    path('repair-categories/<int:pk>/delete/', views.RepairCategoryDeleteView.as_view(), name='repair_category_delete'),

    path('repairs/', views.RepairListView.as_view(), name='repair_list'),
    path('repairs/add/', views.RepairCreateView.as_view(), name='repair_add'),
    path('repairs/<int:pk>/', views.RepairDetailView.as_view(), name='repair_detail'),
    path('repairs/<int:pk>/edit/', views.RepairUpdateView.as_view(), name='repair_edit'),
    path('repairs/<int:pk>/delete/', views.RepairDeleteView.as_view(), name='repair_delete'),
    path('repairs/<int:pk>/photo/add/', views.repair_photo_add, name='repair_photo_add'),
    path('repairs/photo/<int:pk>/delete/', views.repair_photo_delete, name='repair_photo_delete'),
    path('repairs/<int:pk>/consume/', views.repair_consume_item, name='repair_consume_item'),
    path('repairs/consumed/<int:pk>/delete/', views.repair_consumed_item_delete, name='repair_consumed_item_delete'),

    path('equipment/', views.EquipmentListView.as_view(), name='equipment_list'),
    path('equipment/add/', views.EquipmentCreateView.as_view(), name='equipment_add'),
    path('equipment/<int:pk>/', views.EquipmentDetailView.as_view(), name='equipment_detail'),
    path('equipment/<int:pk>/edit/', views.EquipmentUpdateView.as_view(), name='equipment_edit'),
    path('equipment/<int:pk>/delete/', views.EquipmentDeleteView.as_view(), name='equipment_delete'),
    path('vendors/', views.VendorListView.as_view(), name='vendor_list'),
    path('vendors/add/', views.VendorCreateView.as_view(), name='vendor_add'),
    path('vendors/<int:pk>/', views.VendorDetailView.as_view(), name='vendor_detail'),
    path('vendors/<int:pk>/edit/', views.VendorUpdateView.as_view(), name='vendor_edit'),
    path('vendors/<int:pk>/delete/', views.VendorDeleteView.as_view(), name='vendor_delete'),

    path('jobs/', views.JobListView.as_view(), name='job_list'),
    path('jobs/add/', views.JobCreateView.as_view(), name='job_add'),
    path('jobs/<int:pk>/', views.JobDetailView.as_view(), name='job_detail'),
    path('jobs/<int:pk>/edit/', views.JobUpdateView.as_view(), name='job_edit'),
    path('jobs/<int:pk>/delete/', views.JobDeleteView.as_view(), name='job_delete'),
    path('jobs/<int:pk>/requirements/add/', views.requirement_add, name='requirement_add'),
    path('jobs/<int:pk>/rfq/create/', views.rfq_create, name='rfq_create'),

    path('requirements/<int:pk>/', views.RequirementDetailView.as_view(), name='requirement_detail'),
    path('requirements/<int:pk>/edit/', views.RequirementUpdateView.as_view(), name='requirement_edit'),
    path('requirements/<int:pk>/delete/', views.requirement_delete, name='requirement_delete'),
    path('requirements/<int:pk>/rfq-vendor/', views.requirement_set_rfq_vendor, name='requirement_set_rfq_vendor'),
    path('requirements/<int:pk>/options/add/', views.option_add, name='option_add'),

    path('options/<int:pk>/edit/', views.OptionUpdateView.as_view(), name='option_edit'),
    path('options/<int:pk>/delete/', views.option_delete, name='option_delete'),
    path('options/<int:pk>/order/', views.option_order, name='option_order'),

    path('catalog/', views.CatalogSourceListView.as_view(), name='catalog_source_list'),
    path('catalog/add/', views.CatalogSourceCreateView.as_view(), name='catalog_source_add'),
    path('catalog/<int:pk>/', views.CatalogSourceDetailView.as_view(), name='catalog_source_detail'),
    path('catalog/<int:pk>/edit/', views.CatalogSourceUpdateView.as_view(), name='catalog_source_edit'),
    path('catalog/<int:pk>/delete/', views.CatalogSourceDeleteView.as_view(), name='catalog_source_delete'),
    path('catalog/sections/<int:pk>/', views.CatalogSectionDetailView.as_view(), name='catalog_section_detail'),

    path('rfqs/', views.RfqListView.as_view(), name='rfq_list'),
    path('rfqs/<int:pk>/', views.RfqDetailView.as_view(), name='rfq_detail'),
    path('rfqs/<int:pk>/csv/', views.rfq_csv, name='rfq_csv'),
    path('rfqs/<int:pk>/sent/', views.rfq_mark_sent, name='rfq_mark_sent'),
    path('rfqs/<int:pk>/draft/', views.rfq_mark_draft, name='rfq_mark_draft'),
    path('rfqs/<int:pk>/delete/', views.rfq_delete, name='rfq_delete'),
]
