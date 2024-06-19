from django.urls import path

from .views import EventListView, EventDetailView, sync_calendar_events, EventDetailUpdateView, EventDetailCreateView, \
    EventDetailDeleteView, EventMyList, GenerateBlogView, EventDetailList, EventDetailPastList, EventCreateView, \
    EventDeleteView

app_name = 'event'
urlpatterns = [
    path('', EventListView.as_view(), name='list'),
    path('create/', EventCreateView.as_view(), name='create'),
    path('delete/<int:pk>/', EventDeleteView.as_view(), name='delete'),
    path('my_list/', EventMyList.as_view(), name='my_list'),
    path('sync/', sync_calendar_events, name='sync_calendar_events'),
    # detail
    path('<int:event_pk>/detail/create/', EventDetailCreateView.as_view(), name='detail_create'),
    path('detail/<int:pk>/', EventDetailView.as_view(), name='detail'),
    path('detail/<int:pk>/update/', EventDetailUpdateView.as_view(), name='detail_update'),
    path('detail/<int:pk>/delete/', EventDetailDeleteView.as_view(), name='detail_delete'),
    path('detail/list/', EventDetailList.as_view(), name='detail_list'),
    path('detail/past_list/', EventDetailPastList.as_view(), name='detail_past_list'),
    path('generate_blog/<int:pk>/', GenerateBlogView.as_view(), name='generate_blog'),
]
