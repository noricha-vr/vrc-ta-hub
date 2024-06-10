from django.views.static import serve
from django.conf import settings
from django.conf.urls.static import static
# urls.py
from django.urls import path
from django.views.generic import TemplateView

app_name = 'ta_hub'
urlpatterns = [
                  path('', TemplateView.as_view(template_name='ta_hub/index.html'), name='index'),
                  path('about/', TemplateView.as_view(template_name='ta_hub/about.html'), name='about'),
                  path('manifest.json', serve,
                       {'path': 'site.webmanifest', 'document_root': settings.BASE_DIR / 'site'}),
              ] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
