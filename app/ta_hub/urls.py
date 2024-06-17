from django.views.static import serve
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from django.views.generic import TemplateView
from ta_hub.views import favicon_view, apple_touch_icon_view

app_name = 'ta_hub'
urlpatterns = [
                  path('', TemplateView.as_view(template_name='ta_hub/index.html'), name='index'),
                  path('about/', TemplateView.as_view(template_name='ta_hub/about.html'), name='about'),
                  path('how_to/', TemplateView.as_view(template_name='ta_hub/how_to.html'), name='how_to'),
                  path('manifest.json', serve,
                       {'path': 'site.webmanifest', 'document_root': settings.BASE_DIR / 'site'}),
                  # favicon
                  path('favicon.ico', favicon_view),
                  path('apple-touch-icon.png', apple_touch_icon_view),
                  path('apple-touch-icon-precomposed.png', apple_touch_icon_view),
                  path('apple-touch-icon-152x152.png', apple_touch_icon_view),
                  path('apple-touch-icon-152x152-precomposed.png', apple_touch_icon_view),
              ] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
