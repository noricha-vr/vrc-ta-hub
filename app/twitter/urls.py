# twitter/urls.py

from django.urls import path

from .views import TwitterTemplateCreateView, TwitterTemplateUpdateView, TwitterTemplateListView, \
    TwitterTemplateDeleteView

app_name = 'twitter'

urlpatterns = [
    path('template/create/', TwitterTemplateCreateView.as_view(), name='template_create'),
    path('template/<int:pk>/update/', TwitterTemplateUpdateView.as_view(), name='template_update'),
    path('template/list/', TwitterTemplateListView.as_view(), name='template_list'),
    path('template/<int:pk>/delete/', TwitterTemplateDeleteView.as_view(), name='template_delete'),
]
