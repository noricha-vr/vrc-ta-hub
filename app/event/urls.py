from django.urls import path
from django.views.generic import TemplateView
from .views import EventListView, EventDetailView, sync_calendar_events, EventDetailUpdateView, EventDetailCreateView, \
    EventDetailDeleteView, EventMyList, GenerateBlogView

app_name = 'event'
urlpatterns = [
    path('', EventListView.as_view(), name='list'),
    path('<int:event_pk>/detail/create/', EventDetailCreateView.as_view(), name='detail_create'),
    path('detail/<int:pk>/', EventDetailView.as_view(), name='detail'),
    path('detail/<int:pk>/update/', EventDetailUpdateView.as_view(), name='detail_update'),
    path('detail/<int:pk>/delete/', EventDetailDeleteView.as_view(), name='detail_delete'),
    path('my_list/', EventMyList.as_view(), name='my_list'),
    path('generate_blog/', GenerateBlogView.as_view(), name='generate_blog'),
    path('sync/', sync_calendar_events, name='sync_calendar_events'),
]
