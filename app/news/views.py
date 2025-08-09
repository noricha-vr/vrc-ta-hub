from django.shortcuts import get_object_or_404
from django.views.generic import ListView, DetailView

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
        context['categories'] = Category.objects.all()
        return context
