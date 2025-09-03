from django.db import models


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
    
    def get_absolute_thumbnail_url(self, request=None) -> str:
        """
        サムネイルの絶対URLを取得
        
        Args:
            request: HttpRequestオブジェクト
        
        Returns:
            サムネイルの絶対URL、なければデフォルト画像URL
        """
        if self.thumbnail:
            thumbnail_url = self.thumbnail.url
            # すでに絶対URLの場合はそのまま返す
            if thumbnail_url.startswith(('http://', 'https://')):
                return thumbnail_url
            # 相対URLの場合は絶対URLに変換
            if request:
                return request.build_absolute_uri(thumbnail_url)
            # requestがない場合はデフォルトのドメインを使用
            return f"https://vrc-ta-hub.com{thumbnail_url}"
        return "https://data.vrc-ta-hub.com/images/twitter-negipan-1600.jpeg"
