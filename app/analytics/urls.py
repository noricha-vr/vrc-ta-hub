from django.urls import path

from .campaign_views import (
    CampaignCreateView,
    CampaignDeleteView,
    CampaignListView,
    CampaignUpdateView,
)
from .dashboard_views import AnalyticsDashboardView
from .views import sync_analytics

app_name = 'analytics'

urlpatterns = [
    path('sync/', sync_analytics, name='sync'),
    path('dashboard/', AnalyticsDashboardView.as_view(), name='dashboard'),
    path('campaigns/', CampaignListView.as_view(), name='campaign_list'),
    path('campaigns/new/', CampaignCreateView.as_view(), name='campaign_create'),
    path('campaigns/<int:pk>/edit/', CampaignUpdateView.as_view(), name='campaign_update'),
    path('campaigns/<int:pk>/delete/', CampaignDeleteView.as_view(), name='campaign_delete'),
]
