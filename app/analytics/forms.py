"""キャンペーン管理フォーム。"""
import re

from django import forms

from community.models import Community

from .models import Campaign
from .services import accessible_community_ids

# 着地パスとして許可するパターン。
# - '/' で始まる
# - 直後の文字は '/' でも '\\' でもない（オープンリダイレクト変則パターン防止）
# - '/' 単体も許可
_LANDING_PATH_RE = re.compile(r'^/([^/\\].*)?$')


class CampaignForm(forms.ModelForm):
    """Campaign 作成・編集フォーム。

    community の選択肢は accessible_community_ids でサーバー側で絞る（IDOR 防止）。
    """

    class Meta:
        model = Campaign
        fields = [
            'community', 'name', 'utm_source', 'utm_medium',
            'utm_campaign', 'landing_path', 'distributed_at', 'note',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '例: 技術書博 5/10 配布チラシ'}),
            'utm_source': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'flyer'}),
            'utm_medium': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'qr'}),
            'utm_campaign': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '20260510-gishohaku'}),
            'landing_path': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '/'}),
            'distributed_at': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'community': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Fail Safe: user 未指定（view 外での誤用）時は選択肢ゼロにして、誤って他集会を保存できないようにする
        if user is None:
            self.fields['community'].queryset = Community.objects.none()
        else:
            self.fields['community'].queryset = Community.objects.filter(
                id__in=accessible_community_ids(user)
            ).order_by('name')
        # CharField のデフォルト strip=True が末尾の全角スペース等を削ってしまい、
        # RegexValidator をすり抜ける（'flyer　' → 'flyer'）ため、UTM 系は strip しない
        for name in ('utm_source', 'utm_medium', 'utm_campaign'):
            if name in self.fields:
                self.fields[name].strip = False

    def clean_landing_path(self):
        """着地パスは '/' で始まる安全なパスに限定する。

        Open Redirect 経路を断つため、`//`、`/\\`、絶対 URL（http://, https://）等を拒否する。
        URL エンコード変則（`/%2F`, `/%5C`）も実際の URL 組み立て時に正規化されない前提でブロック。
        """
        value = (self.cleaned_data.get('landing_path') or '/').strip()
        # 絶対 URL を拒否
        if value.startswith(('http://', 'https://')):
            raise forms.ValidationError(
                '着地パスは絶対URLではなく / で始まるパスを指定してください。'
            )
        if not value.startswith('/'):
            value = '/' + value
        if not _LANDING_PATH_RE.match(value):
            # `//evil.example`, `/\evil.example` 等のスキーマレス相対 URL を拒否
            raise forms.ValidationError(
                '着地パスは `/` で始まり、直後に `/` や `\\` を含めない形式にしてください。'
            )
        # URL エンコード変則（先頭の %2F / %5C）も同様に拒否
        normalized_head = value[:6].lower()
        if normalized_head.startswith(('/%2f', '/%5c')):
            raise forms.ValidationError(
                '着地パスに不正なエスケープ文字が含まれています。'
            )
        return value
