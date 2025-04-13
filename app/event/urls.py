from django.urls import path
from django.views.generic import TemplateView

from .views import EventListView, EventDetailView, sync_calendar_events, EventDetailUpdateView, EventDetailCreateView, \
    EventDetailDeleteView, EventMyList, GenerateBlogView, EventDetailPastList, \
    EventDeleteView, GoogleCalendarEventCreateView

app_name = 'event'
urlpatterns = [
    path('list/', EventListView.as_view(), name='list'),
    path('delete/<int:pk>/', EventDeleteView.as_view(), name='delete'),
    path('my_list/', EventMyList.as_view(), name='my_list'),
    path('sync/', sync_calendar_events, name='sync_calendar_events'),
    path('calendar/create/', GoogleCalendarEventCreateView.as_view(), name='calendar_create'),
    # detail
    path('<int:event_pk>/detail/create/', EventDetailCreateView.as_view(), name='detail_create'),
    path('detail/<int:pk>/', EventDetailView.as_view(), name='detail'),
    path('detail/<int:pk>/update/', EventDetailUpdateView.as_view(), name='detail_update'),
    path('detail/<int:pk>/delete/', EventDetailDeleteView.as_view(), name='detail_delete'),
    path('detail/history/', EventDetailPastList.as_view(), name='detail_history'),
    path('generate_blog/<int:pk>/', GenerateBlogView.as_view(), name='generate_blog'),
    path('markdown/', TemplateView.as_view(template_name='event/markdown.html'), name='markdown'),
]
