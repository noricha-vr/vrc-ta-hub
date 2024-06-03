from django.urls import path
from django.views.generic import TemplateView
from .views import EventListView, EventDetailView

app_name = 'event'
urlpatterns = [
    path('events/', EventListView.as_view(), name='list'),
    path('events/<int:pk>/', EventDetailView.as_view(), name='detail'),
]
