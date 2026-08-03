from django.db import models
from django.http import HttpRequest
from django.templatetags.static import static

from website.constants import DEFAULT_NEWS_IMAGE_URL, build_site_url


STATIC_THUMBNAIL_BY_SLUG = {
    "vket-2026-summer": "news/images/og/vket-2026-summer-video-archive-v1.png",
}


class Category(models.Model):
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "カテゴリ"
        verbose_name_plural = "カテゴリ"

    def __str__(self) -> str:
        return self.name


class Post(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    body_markdown = models.TextField()
    meta_description = models.TextField(blank=True, help_text="SEO用のメタディスクリプション（空欄の場合は本文から自動生成）")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="posts")
    thumbnail = models.ImageField(upload_to="news/", null=True, blank=True)
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        verbose_name = "記事"
        verbose_name_plural = "記事"

    def __str__(self) -> str:
        return self.title
    
    def get_meta_description(self, max_length: int = 160) -> str:
        """
        メタディスクリプションを取得（キャッシュ可能）
        
        Args:
            max_length: 最大文字数（デフォルト: 160）
        
        Returns:
            メタディスクリプション文字列
        """
        import re
        
        if self.meta_description:
            return self.meta_description[:max_length]
        
        # Markdownから改行とマークダウン記法を除去
        clean_text = re.sub(r'[#*_`\[\]()]', '', self.body_markdown)
        clean_text = clean_text.replace('\n', ' ').replace('\r', '')
        # 複数スペースを単一スペースに
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        
        return clean_text[:max_length]
    
    @property
    def has_detail_thumbnail(self) -> bool:
        """本文に表示するサムネイルの有無を返す。"""
        return bool(self.thumbnail or self.uses_static_thumbnail)

    @property
    def uses_static_thumbnail(self) -> bool:
        """専用staticサムネイルを使用するか返す。"""
        return not self.thumbnail and self.slug in STATIC_THUMBNAIL_BY_SLUG

    def get_absolute_thumbnail_url(self, request: HttpRequest | None = None) -> str:
        """サムネイルの絶対URLを返す。

        Args:
            request: 相対URLのホスト解決に使うリクエスト。
        """
        if self.thumbnail:
            thumbnail_url = self.thumbnail.url
            return self._build_absolute_thumbnail_url(thumbnail_url, request)

        static_thumbnail = STATIC_THUMBNAIL_BY_SLUG.get(self.slug)
        if static_thumbnail:
            return self._build_absolute_thumbnail_url(static(static_thumbnail), request)

        return DEFAULT_NEWS_IMAGE_URL

    @staticmethod
    def _build_absolute_thumbnail_url(
        thumbnail_url: str,
        request: HttpRequest | None = None,
    ) -> str:
        if thumbnail_url.startswith(("http://", "https://")):
            return thumbnail_url
        if request:
            if not thumbnail_url.startswith("/"):
                thumbnail_url = f"/{thumbnail_url}"
            return request.build_absolute_uri(thumbnail_url)
        return build_site_url(thumbnail_url)
