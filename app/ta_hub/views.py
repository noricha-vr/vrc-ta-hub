from django.views.generic import TemplateView, ListView, DetailView


class IndexView(TemplateView):
    template_name = 'ta_hub/index.html'
