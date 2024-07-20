# twitter/urls.py

from django.urls import path

from .views import TwitterTemplateCreateView, TwitterTemplateUpdateView, TwitterTemplateListView, TweetEventView

app_name = 'twitter'

urlpatterns = [
    path('template/create/', TwitterTemplateCreateView.as_view(), name='template_create'),
    path('template/<int:pk>/update/', TwitterTemplateUpdateView.as_view(), name='template_update'),
    path('template/list/', TwitterTemplateListView.as_view(), name='template_list'),
    path('tweet/event/<int:event_pk>/template/<int:template_pk>/', TweetEventView.as_view(),
         name='tweet_event_with_template'),
]
