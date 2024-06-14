from django.views.generic import TemplateView, ListView, DetailView
from django.views.static import serve


class IndexView(TemplateView):
    template_name = 'ta_hub/index.html'


def favicon_view(request):
    return serve(request, 'favicon.ico', document_root='site')


def apple_touch_icon_view(request):
    return serve(request, 'apple-touch-icon.png', document_root='site')
