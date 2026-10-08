from django.urls import path

from . import views


app_name = 'discord_scheduler'

urlpatterns = [
    path('', views.PostListView.as_view(), name='post_list'),
    path('new/', views.PostCreateView.as_view(), name='post_create'),
    path('process/', views.process_posts, name='process_posts'),
    path('<int:pk>/edit/', views.PostEditView.as_view(), name='post_edit'),
    path('<int:pk>/cancel/', views.PostCancelView.as_view(), name='post_cancel'),
]
