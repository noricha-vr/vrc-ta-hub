from django.urls import path
from .views import CommunityListView, CommunityDetailView, CommunityUpdateView

app_name = 'community'
urlpatterns = [
    path('list/', CommunityListView.as_view(), name='list'),
    path('<int:pk>/', CommunityDetailView.as_view(), name='detail'),
    path('update/<int:pk>/', CommunityUpdateView.as_view(), name='update'),

]
