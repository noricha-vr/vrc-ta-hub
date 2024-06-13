"""website URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import path
from django.views.generic import TemplateView, RedirectView
from .views import SitemapView

app_name = 'sitemap'

# Redirect settings
favicon_view = RedirectView.as_view(
    url='https://data.vrc-ta-hub.com/images%2Fsite%2Ffavicon.ico',
    permanent=True)
icon120_view = RedirectView.as_view(
    url='https://data.vrc-ta-hub.com/images%2Fsite%2Fapple-touch-icon.png',
    permanent=True)
icon160_view = RedirectView.as_view(
    url='https://data.vrc-ta-hub.com/images%2Fsite%2Fapple-touch-icon.png',
    permanent=True)
urlpatterns = [
    # path('sitemap.xml', SitemapListView.as_view(), name='sitemap_list'),
    path('favicon.ico', favicon_view),
    path('robots.txt', TemplateView.as_view(template_name='sitemap/robots.txt')),
    path('apple-touch-icon.png', icon120_view),
    path('apple-touch-icon-precomposed.png', icon120_view),
    path('apple-touch-icon-152x152.png', icon160_view),
    path('apple-touch-icon-152x152-precomposed.png', icon160_view),
    path('sitemap.xml', SitemapView.as_view(), name='sitemap'),
    path('sitemaps.xml', RedirectView.as_view(url='/sitemap.xml', permanent=True)),
]
