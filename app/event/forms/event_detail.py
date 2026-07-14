"""EventDetail (発表詳細) フォーム。"""

from django import forms

from ..datetime_lock import (
    EVENT_DETAIL_DATETIME_LOCK_MESSAGE,
    has_event_detail_duration_changed,
    has_event_detail_start_time_changed,
    is_event_detail_datetime_locked,
)
from ..models import EventDetail
from ..thumbnail import SLIDE_THUMBNAIL_ASPECT_RATIO_TEXT
from .mixins import EventDetailMediaFormMixin


class EventDetailForm(EventDetailMediaFormMixin, forms.ModelForm):
    start_time = forms.TimeField(
        label='開始時間',
        widget=forms.TimeInput(
            attrs={'type': 'time', 'class': 'form-control w-25'})  # Bootstrapのクラスを追加
    )
    duration = forms.IntegerField(
        label='発表の持ち時間（分）',
        min_value=1,
        widget=forms.NumberInput(attrs={'class': 'form-control w-25'})
    )

    # フィールドのラベルをオーバーライド
    theme = forms.CharField(
        label='テーマ',
        required=False,  # 特別企画とブログでは不要なため
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    h1 = forms.CharField(
        label='タイトル(H1)',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    contents = forms.CharField(
        label='内容',
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': '8'})
    )

    # 記事自動生成チェックボックス
    generate_blog_article = forms.BooleanField(
        label='記事を自動生成する',
        required=False,
        initial=True,  # デフォルトON
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input form-check-input-lg'}),
        help_text='PDFまたはYouTube URLが設定されている場合、AIによって記事を自動生成します'
    )

    class Meta:
        model = EventDetail
        fields = ['detail_type', 'theme', 'speaker', 'start_time', 'duration', 'slide_file', 'slide_url',
                  'thumbnail_image', 'youtube_url', 'h1', 'contents', 'generate_blog_article']
        widgets = {
            'detail_type': forms.RadioSelect(attrs={'class': 'form-check-input'}),
            'youtube_url': forms.URLInput(attrs={'class': 'form-control'}),
            'slide_url': forms.URLInput(attrs={'class': 'form-control'}),
            'slide_file': forms.ClearableFileInput(attrs={'class': 'form-control-file', 'accept': '.pdf'}),
            'thumbnail_image': forms.ClearableFileInput(attrs={'class': 'form-control-file', 'accept': 'image/*'}),
            'speaker': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'VRChat表示名を入力'
            }),
            'start_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control w-25'}),
            'duration': forms.NumberInput(attrs={'class': 'form-control w-25'}),
            'theme': forms.TextInput(attrs={'class': 'form-control'}),
            'h1': forms.TextInput(attrs={'class': 'form-control'}),
            'contents': forms.Textarea(attrs={'class': 'form-control', 'rows': '8'}),
        }
        help_texts = {
            'detail_type': '※ 記事の種類を選択してください。',
            'contents': '※ Markdown形式で記述してください。',
            'h1': '※ 空のときはテーマが使われます。',
            'duration': '単位は分',
            'youtube_url': 'YouTubeのURLの他、Discordのメッセージへのリンクも入力できます。',
            'slide_file': '※ 記事生成に使うPDFです。まずここにスライドPDFをアップロードしてください（最大30MB）。',
            'slide_url': '※ 任意。記事生成後、公開用の外部スライドURLがある場合に貼り付けてください。URL入力のみでは記事は生成されません。',
            'thumbnail_image': (
                f'※ 記事ページの上部に表示される画像です。'
                f'アップロード時にスライドと同じ横長の比率（{SLIDE_THUMBNAIL_ASPECT_RATIO_TEXT}）へ自動トリミングします。'
                'はみ出した部分は中央基準で切り取られます。'
                '未設定でPDFがある場合はPDF保存時または記事生成時に自動設定されます。'
            ),
        }

    # start_time と duration の初期値はEventCreateFormと同じにする
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        self.datetime_locked = False
        self.applicant_datetime_locked = False
        self.datetime_lock_message = EVENT_DETAIL_DATETIME_LOCK_MESSAGE

        if not self.instance.pk and self.request and self.request.user.is_authenticated:
            membership = self.request.user.community_memberships.select_related('community').first()
            if membership:
                self.fields['start_time'].initial = membership.community.start_time
            self.fields['duration'].initial = 30

        if (
            self.instance.pk
            and self.request
            and is_event_detail_datetime_locked(self.instance, self.request.user)
        ):
            self.datetime_locked = True
            self.fields['start_time'].disabled = True
            self.fields['duration'].disabled = True

        if self.instance.pk and self.request:
            # views.helpers は views パッケージ初期化時にこのフォームを import するため、
            # 循環 import を避けてフォーム生成時に読み込む。
            from event.views.helpers import is_event_detail_admin

            self.applicant_datetime_locked = not is_event_detail_admin(
                self.request.user, self.instance
            )
            if self.applicant_datetime_locked:
                self.fields['start_time'].disabled = True
                self.fields['duration'].disabled = True

        # 既存の記事がある場合（更新時）は自動生成チェックボックスをOFFにする
        if self.instance and self.instance.pk:
            # meta_descriptionが存在する場合は記事が既に生成されていると判断
            if self.instance.meta_description:
                self.fields['generate_blog_article'].initial = False


    def clean(self):
        cleaned_data = super().clean()
        detail_type = cleaned_data.get('detail_type')
        is_datetime_locked = (
            self.instance.pk
            and self.request
            and is_event_detail_datetime_locked(self.instance, self.request.user)
        )

        if is_datetime_locked:
            if has_event_detail_start_time_changed(self.instance, self.data.get('start_time')):
                self.add_error('start_time', EVENT_DETAIL_DATETIME_LOCK_MESSAGE)
            if has_event_detail_duration_changed(self.instance, self.data.get('duration')):
                self.add_error('duration', EVENT_DETAIL_DATETIME_LOCK_MESSAGE)

        # 特別企画とブログの場合、非表示フィールドにデフォルト値を設定
        if detail_type == 'SPECIAL':
            # 特別企画のデフォルト値
            cleaned_data['theme'] = 'Special Event'
            cleaned_data['speaker'] = ''
            # start_timeは入力されたものを使用（ただしVketロック中は元の値を維持）
            if not getattr(self, 'datetime_locked', False):
                cleaned_data['duration'] = 60
        elif detail_type == 'BLOG':
            # ブログのデフォルト値（h1があればthemeにコピー）
            h1 = cleaned_data.get('h1', '')
            cleaned_data['theme'] = h1 if h1 else 'Blog'
            cleaned_data['speaker'] = ''
            if not getattr(self, 'datetime_locked', False):
                cleaned_data['start_time'] = self.instance.event.start_time if self.instance.pk else self.fields['start_time'].initial
                cleaned_data['duration'] = 30

        if is_datetime_locked:
            cleaned_data['start_time'] = self.instance.start_time
            cleaned_data['duration'] = self.instance.duration

        if self.applicant_datetime_locked:
            cleaned_data['start_time'] = self.instance.start_time
            cleaned_data['duration'] = self.instance.duration

        return cleaned_data
