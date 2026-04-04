# twitter/urls.py

from django.urls import path

from .views import (
    TwitterTemplateCreateView,
    TwitterTemplateUpdateView,
    TwitterTemplateListView,
    TwitterTemplateDeleteView,
    TweetEventWithTemplateView,
    post_scheduled_tweets,
)

app_name = 'twitter'

urlpatterns = [
    path('template/create/', TwitterTemplateCreateView.as_view(), name='template_create'),
    path('template/<int:pk>/update/', TwitterTemplateUpdateView.as_view(), name='template_update'),
    path('template/list/', TwitterTemplateListView.as_view(), name='template_list'),
    path('template/<int:pk>/delete/', TwitterTemplateDeleteView.as_view(), name='template_delete'),
    path('tweet/<int:event_pk>/<int:template_pk>/', TweetEventWithTemplateView.as_view(),
         name='tweet_event_with_template'),
    path('post-scheduled/', post_scheduled_tweets, name='post_scheduled_tweets'),
]
