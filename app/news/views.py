from django.shortcuts import get_object_or_404
from django.views.generic import ListView, DetailView
from django.views.decorators.cache import cache_page
from django.utils.decorators import method_decorator
from django.core.cache import cache

from .models import Post, Category


class PostListView(ListView):
    model = Post
    template_name = "news/list.html"
    paginate_by = 10

    def get_queryset(self):
        return (
            Post.objects.select_related("category")
            .filter(is_published=True)
            .order_by("-published_at", "-created_at")
        )
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # カテゴリー一覧を1時間キャッシュ
        categories = cache.get('news_categories')
        if categories is None:
            categories = list(Category.objects.all().order_by('order'))
            cache.set('news_categories', categories, 3600)  # 1時間キャッシュ
        context['categories'] = categories
        return context


class PostDetailView(DetailView):
    model = Post
    template_name = "news/detail.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return Post.objects.select_related("category").filter(is_published=True)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # requestをモデルメソッドで使用できるように追加
        context['request'] = self.request
        return context


class CategoryListView(ListView):
    model = Post
    template_name = "news/category_list.html"
    paginate_by = 10
    
    def get_queryset(self):
        self.category = get_object_or_404(Category, slug=self.kwargs['category_slug'])
        return (
            Post.objects.select_related("category")
            .filter(is_published=True, category=self.category)
            .order_by("-published_at", "-created_at")
        )
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['category'] = self.category
        # カテゴリー一覧を1時間キャッシュ（PostListViewと同じキャッシュを使用）
        categories = cache.get('news_categories')
        if categories is None:
            categories = list(Category.objects.all().order_by('order'))
            cache.set('news_categories', categories, 3600)  # 1時間キャッシュ
        context['categories'] = categories
        return context
