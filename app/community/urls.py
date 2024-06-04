from django.urls import path
from .views import CommunityListView, CommunityDetailView

app_name = 'community'
urlpatterns = [
    path('list/', CommunityListView.as_view(), name='list'),
    path('<int:pk>/', CommunityDetailView.as_view(), name='detail'),
]
