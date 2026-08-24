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
]
