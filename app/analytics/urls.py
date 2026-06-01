from django.urls import path

from .views import sync_analytics

app_name = 'analytics'

urlpatterns = [
    path('sync/', sync_analytics, name='sync'),
]
