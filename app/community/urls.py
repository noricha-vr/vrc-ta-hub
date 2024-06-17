from django.urls import path
from .views import CommunityListView, CommunityDetailView, CommunityUpdateView, WaitingCommunityListView, AcceptView

app_name = 'community'
urlpatterns = [
    path('list/', CommunityListView.as_view(), name='list'),
    path('waiting_list/', WaitingCommunityListView.as_view(), name='waiting_list'),
    path('<int:pk>/', CommunityDetailView.as_view(), name='detail'),
    path('update/<int:pk>/', CommunityUpdateView.as_view(), name='update'),
    path('accept/', AcceptView.as_view(), name='accept'),
]
