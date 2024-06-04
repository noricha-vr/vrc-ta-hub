from django.urls import path
from django.views.generic import TemplateView
from .views import EventListView, EventDetailView

app_name = 'event'
urlpatterns = [
    path('list/', EventListView.as_view(), name='list'),
    path('<int:pk>/', EventDetailView.as_view(), name='detail'),
]
