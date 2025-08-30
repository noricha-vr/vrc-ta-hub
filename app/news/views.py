import json
import logging
from django.contrib.auth.mixins import UserPassesTestMixin
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.views.decorators.cache import cache_page
from django.utils.decorators import method_decorator
from django.core.cache import cache

from .models import Post, Category
from .forms import PostForm

logger = logging.getLogger(__name__)

class PostListView(ListView):
    model = Post
    template_name = "news/list.html"
    paginate_by = 10

    def get_queryset(self):
        queryset = Post.objects.select_related("category")
        # スタッフ以外は公開記事のみ表示
        if not (self.request.user.is_authenticated and self.request.user.is_staff):
            queryset = queryset.filter(is_published=True)
        return queryset.order_by("-published_at", "-created_at")
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # カテゴリー一覧を1時間キャッシュ
        categories = cache.get('news_categories')
        if categories is None:
            categories = list(Category.objects.all().order_by('order'))
            cache.set('news_categories', categories, 3600)  # 1時間キャッシュ
        context['categories'] = categories

        # 構造化データ（BreadcrumbList + CollectionPage）
        try:
            request = self.request
            home_url = request.build_absolute_uri('/')
            list_url = request.build_absolute_uri(reverse('news:list'))

            breadcrumbs = {
                "@context": "https://schema.org",
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "ホーム", "item": home_url},
                    {"@type": "ListItem", "position": 2, "name": "お知らせ", "item": list_url},
                ],
            }

            items = []
            for idx, post in enumerate(context.get('object_list', []), start=1):
                post_url = request.build_absolute_uri(
                    reverse('news:detail', kwargs={'slug': post.slug})
                )
                item = {
                    "@type": "ListItem",
                    "position": idx,
                    "name": post.title,
                    "url": post_url,
                }
                thumb = post.get_absolute_thumbnail_url(request)
                if thumb:
                    item["image"] = thumb
                items.append(item)

            collection = {
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                "name": "お知らせ",
                "url": list_url,
                "inLanguage": "ja-JP",
                "isPartOf": home_url,
                "mainEntity": {
                    "@type": "ItemList",
                    "itemListElement": items,
                },
            }

            context['structured_data_json'] = json.dumps([breadcrumbs, collection], ensure_ascii=False)
            logger.info("Structured data prepared for News List page")
        except Exception as e:
            logger.warning(f"Failed to prepare structured data for News List: {str(e)}")
        return context


class PostDetailView(DetailView):
    model = Post
    template_name = "news/detail.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        queryset = Post.objects.select_related("category")
        # スタッフ以外は公開記事のみ表示
        if not (self.request.user.is_authenticated and self.request.user.is_staff):
            queryset = queryset.filter(is_published=True)
        return queryset
    
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
        queryset = Post.objects.select_related("category").filter(category=self.category)
        # スタッフ以外は公開記事のみ表示
        if not (self.request.user.is_authenticated and self.request.user.is_staff):
            queryset = queryset.filter(is_published=True)
        return queryset.order_by("-published_at", "-created_at")
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['category'] = self.category
        # カテゴリー一覧を1時間キャッシュ（PostListViewと同じキャッシュを使用）
        categories = cache.get('news_categories')
        if categories is None:
            categories = list(Category.objects.all().order_by('order'))
            cache.set('news_categories', categories, 3600)  # 1時間キャッシュ
        context['categories'] = categories

        # 構造化データ（BreadcrumbList + CollectionPage）
        try:
            request = self.request
            home_url = request.build_absolute_uri('/')
            list_url = request.build_absolute_uri(reverse('news:list'))
            category_url = request.build_absolute_uri(
                reverse('news:category_list', kwargs={'category_slug': self.category.slug})
            )

            breadcrumbs = {
                "@context": "https://schema.org",
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "ホーム", "item": home_url},
                    {"@type": "ListItem", "position": 2, "name": "お知らせ", "item": list_url},
                    {"@type": "ListItem", "position": 3, "name": self.category.name, "item": category_url},
                ],
            }

            items = []
            for idx, post in enumerate(context.get('object_list', []), start=1):
                post_url = request.build_absolute_uri(
                    reverse('news:detail', kwargs={'slug': post.slug})
                )
                item = {
                    "@type": "ListItem",
                    "position": idx,
                    "name": post.title,
                    "url": post_url,
                }
                thumb = post.get_absolute_thumbnail_url(request)
                if thumb:
                    item["image"] = thumb
                items.append(item)

            collection = {
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                "name": f"お知らせ - {self.category.name}",
                "url": category_url,
                "inLanguage": "ja-JP",
                "isPartOf": list_url,
                "mainEntity": {
                    "@type": "ItemList",
                    "itemListElement": items,
                },
            }

            context['structured_data_json'] = json.dumps([breadcrumbs, collection], ensure_ascii=False)
            logger.info(f"Structured data prepared for News Category page: {self.category.slug}")
        except Exception as e:
            logger.warning(f"Failed to prepare structured data for News Category: {str(e)}")
        return context


class StaffRequiredMixin(UserPassesTestMixin):
    """スタッフ権限が必要なビューのMixin"""
    
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_staff


class PostCreateView(StaffRequiredMixin, CreateView):
    """記事作成ビュー（スタッフのみ）"""
    model = Post
    form_class = PostForm
    template_name = "news/post_form.html"
    
    def get_success_url(self):
        return reverse('news:detail', kwargs={'slug': self.object.slug})
    
    def form_valid(self, form):
        response = super().form_valid(form)
        # キャッシュをクリア
        cache.delete('news_categories')
        return response


class PostUpdateView(StaffRequiredMixin, UpdateView):
    """記事編集ビュー（スタッフのみ）"""
    model = Post
    form_class = PostForm
    template_name = "news/post_form.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"
    
    def get_success_url(self):
        return reverse('news:detail', kwargs={'slug': self.object.slug})
    
    def form_valid(self, form):
        response = super().form_valid(form)
        # キャッシュをクリア
        cache.delete('news_categories')
        return response


class PostDeleteView(StaffRequiredMixin, DeleteView):
    """記事削除ビュー（スタッフのみ）"""
    model = Post
    template_name = "news/post_confirm_delete.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"
    success_url = reverse_lazy('news:list')
    
    def form_valid(self, form):
        response = super().form_valid(form)
        # キャッシュをクリア
        cache.delete('news_categories')
        return response
