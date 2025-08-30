from django.urls import path
from django.views.generic import RedirectView
from . import views

app_name = 'news'

urlpatterns = [
    path('', views.PostListView.as_view(), name='list'),
    path('create/', views.PostCreateView.as_view(), name='create'),
    # 旧URLから新URLへのリダイレクト（互換性維持）
    path('category/record/', 
         RedirectView.as_view(url='/news/category/activity/', 
                              permanent=True),
         name='record_redirect'),
    path('category/<slug:category_slug>/', views.CategoryListView.as_view(), name='category_list'),
    path('<slug:slug>/', views.PostDetailView.as_view(), name='detail'),
    path('<slug:slug>/edit/', views.PostUpdateView.as_view(), name='edit'),
    path('<slug:slug>/delete/', views.PostDeleteView.as_view(), name='delete'),
]
