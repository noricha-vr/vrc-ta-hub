from django.urls import path

from .dashboard_views import AnalyticsDashboardView
from .views import sync_analytics

app_name = 'analytics'

urlpatterns = [
    path('sync/', sync_analytics, name='sync'),
    path('dashboard/', AnalyticsDashboardView.as_view(), name='dashboard'),
]
