import json
import logging
from django.shortcuts import get_object_or_404
from django.views.generic import ListView, DetailView
from django.views.decorators.cache import cache_page
from django.utils.decorators import method_decorator
from django.core.cache import cache

from .models import Post, Category

logger = logging.getLogger(__name__)

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

        # 構造化データ（BlogPosting）を生成
        try:
            post: Post = self.object
            request = self.request
            absolute_url = request.build_absolute_uri()
            image_url = post.get_absolute_thumbnail_url(request)

            publisher_obj = {
                "@type": "Organization",
                "name": "VRChat 技術・学術系イベントHub",
                "logo": {
                    "@type": "ImageObject",
                    "url": "https://data.vrc-ta-hub.com/images/twitter-negipan-1600.jpeg",
                },
            }

            description = post.get_meta_description()

            structured_data = {
                "@context": "https://schema.org",
                "@type": "BlogPosting",
                "mainEntityOfPage": {"@type": "WebPage", "@id": absolute_url},
                "headline": post.title,
                "description": description,
                "url": absolute_url,
                "inLanguage": "ja-JP",
                "isAccessibleForFree": True,
                "datePublished": (post.published_at or post.created_at).isoformat(),
                "dateModified": post.updated_at.isoformat(),
                "publisher": publisher_obj,
                "author": publisher_obj,  # 著者情報がないため、運営を著者として設定
                "articleSection": post.category.name,
            }

            if image_url:
                structured_data["image"] = [image_url]

            context['structured_data_json'] = json.dumps(structured_data, ensure_ascii=False)
            logger.info(f"Structured data prepared for News Post: slug={post.slug}")
        except Exception as e:
            logger.warning(f"Failed to prepare structured data for News Post: {str(e)}")

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
