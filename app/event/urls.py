from django.urls import path
from django.views.generic import TemplateView
from .views import EventListView, EventDetailView, import_events

app_name = 'event'
urlpatterns = [
    path('list/', EventListView.as_view(), name='list'),
    path('<int:pk>/', EventDetailView.as_view(), name='detail'),
    path('import/', import_events, name='import'),
]
