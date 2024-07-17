from django.urls import path

from event_calendar.views import CalendarEntryUpdateView
from .views import CommunityListView, CommunityDetailView, CommunityUpdateView, WaitingCommunityListView, AcceptView

app_name = 'community'
urlpatterns = [
    path('list/', CommunityListView.as_view(), name='list'),
    path('calendar_update/<int:pk>/', CalendarEntryUpdateView.as_view(), name='calendar_update'),
    path('waiting_list/', WaitingCommunityListView.as_view(), name='waiting_list'),
    path('<int:pk>/', CommunityDetailView.as_view(), name='detail'),
    path('update/<int:pk>/', CommunityUpdateView.as_view(), name='update'),
    path('accept/', AcceptView.as_view(), name='accept'),
]
