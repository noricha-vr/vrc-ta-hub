from django.views.generic import TemplateView, ListView, DetailView
from .models import Event


class EventListView(ListView):
    model = Event
    template_name = 'event/list.html'
    context_object_name = 'events'


class EventDetailView(DetailView):
    model = Event
    template_name = 'event/detail.html'
    context_object_name = 'event'
