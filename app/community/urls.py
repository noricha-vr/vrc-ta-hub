from django.urls import path

from event_calendar.views import CalendarEntryUpdateView
from .views import CommunityListView, CommunityDetailView, CommunityUpdateView, WaitingCommunityListView, AcceptView, RejectView, CloseCommunityView, ReopenCommunityView, ArchivedCommunityListView

app_name = 'community'
urlpatterns = [
    path('list/', CommunityListView.as_view(), name='list'),
    path('archive/', ArchivedCommunityListView.as_view(), name='archive_list'),
    path('calendar_update/', CalendarEntryUpdateView.as_view(), name='calendar_update'),
    path('waiting_list/', WaitingCommunityListView.as_view(), name='waiting_list'),
    path('<int:pk>/', CommunityDetailView.as_view(), name='detail'),
    path('update/', CommunityUpdateView.as_view(), name='update'),
    path('accept/<int:pk>/', AcceptView.as_view(), name='accept'),
    path('reject/<int:pk>/', RejectView.as_view(), name='reject'),
    path('close/<int:pk>/', CloseCommunityView.as_view(), name='close'),
    path('reopen/<int:pk>/', ReopenCommunityView.as_view(), name='reopen'),
]
