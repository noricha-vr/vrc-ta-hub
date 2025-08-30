from django import forms
from django.utils import timezone

from .models import Post, Category


class PostForm(forms.ModelForm):
    """記事作成・編集フォーム"""
    
    class Meta:
        model = Post
        fields = ['title', 'slug', 'body_markdown', 'meta_description', 
                  'category', 'thumbnail', 'is_published']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'body_markdown': forms.Textarea(attrs={'class': 'form-control', 'rows': 15}),
            'meta_description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'thumbnail': forms.FileInput(attrs={'class': 'form-control'}),
            'is_published': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'title': 'タイトル',
            'slug': 'スラッグ（URL用）',
            'body_markdown': '本文（Markdown形式）',
            'meta_description': 'メタディスクリプション（SEO用）',
            'category': 'カテゴリ',
            'thumbnail': 'サムネイル画像',
            'is_published': '公開する',
        }
        help_texts = {
            'slug': 'URLに使用される文字列です。英数字とハイフンのみ使用可能',
            'body_markdown': 'Markdown記法で記述してください',
            'meta_description': '空欄の場合は本文から自動生成されます',
        }
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        
        # 公開フラグがONで公開日時が未設定の場合、現在日時を設定
        if instance.is_published and not instance.published_at:
            instance.published_at = timezone.now()
        # 公開フラグがOFFの場合、公開日時をクリア
        elif not instance.is_published:
            instance.published_at = None
            
        if commit:
            instance.save()
        return instance