"""
URL configuration for djangoshipshape project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
"""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', include('inventory.urls')),
    # Uploaded photos/PDFs, served by Django itself even with DEBUG off:
    # there's no nginx in front of the app on the server, and for one user
    # over Tailscale this is plenty fast. Unlike static(), this stays behind
    # LoginRequiredMiddleware, so photos need a login like every other page.
    re_path(rf'^{settings.MEDIA_URL.strip("/")}/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]
