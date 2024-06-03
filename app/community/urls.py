from django.urls import path
from .views import CommunityListView, CommunityDetailView

app_name = 'community'
urlpatterns = [
    path('communities/', CommunityListView.as_view(), name='list'),
    path('communities/<int:pk>/', CommunityDetailView.as_view(), name='detail'),
]
