from django.urls import path
from django.views.generic import TemplateView
from .views import EventListView, EventDetailView, sync_calendar_events

app_name = 'event'
urlpatterns = [
    path('list/', EventListView.as_view(), name='list'),
    path('detail/<int:pk>/', EventDetailView.as_view(), name='detail'),
    path('update/', sync_calendar_events, name='sync_calendar_events'),
]
