from django.urls import path
from . import views

app_name = 'news'

urlpatterns = [
    path('', views.PostListView.as_view(), name='list'),
    path('category/<slug:category_slug>/', views.CategoryListView.as_view(), name='category_list'),
    path('<slug:slug>/', views.PostDetailView.as_view(), name='detail'),
]
