"""EventDetail (発表詳細) フォーム。"""

from django import forms
from django.db import transaction

from user_account.forms import normalize_x_account

from ..datetime_lock import (
    EVENT_DETAIL_DATETIME_LOCK_MESSAGE,
    has_event_detail_duration_changed,
    has_event_detail_start_time_changed,
    is_event_detail_datetime_locked,
)
from ..models import EventDetail
from ..services.lt_schedule import next_lt_start_time
from ..thumbnail import SLIDE_THUMBNAIL_ASPECT_RATIO_TEXT
from .mixins import (
    ARTICLE_CONSENT_CHOICES,
    ARTICLE_CONSENT_HELP_TEXT,
    ARTICLE_CONSENT_LABEL,
    ARTICLE_CONSENT_REQUIRED_MESSAGE,
    RECORDING_POLICY_HELP_TEXT,
    RECORDING_POLICY_LABEL,
    EventDetailMediaFormMixin,
    RecordingPolicyFormMixin,
    recording_policy_widget,
    configure_article_generation_field,
    article_consent_widget,
    validate_additional_info,
)


class EventDetailForm(EventDetailMediaFormMixin, RecordingPolicyFormMixin, forms.ModelForm):
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
    x_account = forms.CharField(
        label='X (Twitter) アカウント',
        max_length=64,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control', 'placeholder': 'noricha_vr', 'autocomplete': 'off',
        }),
        help_text='Hub の X の告知に @ID を載せます。URL でも可。発表者名があなたの表示名と同じ時だけ使います。',
    )
    article_consent = forms.ChoiceField(
        label=ARTICLE_CONSENT_LABEL,
        choices=ARTICLE_CONSENT_CHOICES,
        initial=EventDetail.ArticleConsent.OK,
        required=False,
        widget=article_consent_widget(),
        help_text=ARTICLE_CONSENT_HELP_TEXT,
    )
    additional_info = forms.CharField(
        label='追加情報',
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control', 'rows': 6, 'aria-describedby': 'id_additional_info_help',
        }),
        help_text='追加で伝えたい情報があれば入力してください。公開されるので、連絡先などは書かないでください。',
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
        fields = ['detail_type', 'theme', 'speaker', 'start_time', 'duration', 'x_account',
                  'recording_policy', 'article_consent', 'additional_info', 'slide_file', 'slide_source_file',
                  'slide_url', 'thumbnail_image', 'youtube_url', 'h1', 'contents', 'generate_blog_article']
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
            'recording_policy': recording_policy_widget(),
        }
        labels = {
            'recording_policy': RECORDING_POLICY_LABEL,
        }
        help_texts = {
            'detail_type': '※ 記事の種類を選択してください。',
            'recording_policy': RECORDING_POLICY_HELP_TEXT,
            'contents': '※ Markdown形式で記述してください。',
            'h1': '※ 空のときはテーマが使われます。',
            'duration': '単位は分',
            'youtube_url': 'YouTubeのURLの他、Discordのメッセージへのリンクも入力できます。',
            'slide_file': '最大 30MB。',
            'slide_url': 'Speaker Deck は PDF を読み込んで記事に使います（PDF は公開せず、1 枚目だけサムネイルにします）。SlideShare などは読み込めないので、記事に使う PDF をアップロードしてください。',
            'thumbnail_image': (
                f'※ 記事ページの上部に表示される画像です。'
                f'アップロード時にスライドと同じ横長の比率（{SLIDE_THUMBNAIL_ASPECT_RATIO_TEXT}）へ自動トリミングします。'
                'はみ出した部分は中央基準で切り取られます。'
                '未設定でPDFがある場合はPDF保存時または記事生成時に自動設定されます。'
            ),
        }

    # 新規登録では申請と同じ初期値を使い、主催者が時刻と持ち時間を調整できる。
    def clean_recording_policy(self):
        # 新規で撮影の選択肢が届かなかった時は、見ていない選択を「公開」にしない（表示後に集会が撮影を許可した等）
        if not self.instance.pk and not self.cleaned_data.get('recording_policy'):
            return EventDetail.RecordingPolicy.FORBIDDEN
        return super().clean_recording_policy()

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        self.datetime_locked = False
        self.applicant_datetime_locked = False
        self.datetime_lock_message = EVENT_DETAIL_DATETIME_LOCK_MESSAGE

        if self.instance.pk:
            for name in ('x_account', 'article_consent', 'additional_info'):
                self.fields.pop(name)
        elif self.instance.event_id:
            community = self.instance.event.community
            self.fields['speaker'].label = '発表者名'
            self.fields['speaker'].widget.attrs['placeholder'] = 'VRChat表示名を入力してください'
            self.fields['theme'].widget.attrs['placeholder'] = '発表テーマを入力してください'
            self.fields['start_time'].initial = next_lt_start_time(self.instance.event)
            self.fields['start_time'].help_text = '前の発表の後（発表がない時は集会の設定）の時刻です。変更できます。'
            self.fields['duration'].initial = community.default_lt_duration
            self.fields['additional_info'].initial = community.lt_application_template
            self.initial.update(
                start_time=self.fields['start_time'].initial,
                duration=community.default_lt_duration,
                additional_info=self.fields['additional_info'].initial,
            )
            if self.request and self.request.user.is_authenticated:
                self.fields['speaker'].initial = self.request.user.display_label
                self.fields['x_account'].initial = self.request.user.x_account
                self.initial['speaker'] = self.request.user.display_label
        elif self.request and self.request.user.is_authenticated:
            membership = self.request.user.community_memberships.select_related('community').first()
            if membership:
                self.fields['start_time'].initial = membership.community.start_time
            self.fields['duration'].initial = 30

        if not self.instance.pk:
            self.initial['article_consent'] = EventDetail.ArticleConsent.OK

        if (
            self.instance.pk
            and self.request
            and is_event_detail_datetime_locked(self.instance, self.request.user)
        ):
            self.datetime_locked = True
            self.fields['start_time'].disabled = True
            self.fields['duration'].disabled = True

        # request がない直接利用ではロックせず、呼び出し側で権限判定済みであることを前提とする。
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

        self.recording_not_allowed = False
        if self.instance.event_id:
            community = self.instance.event.community
            if self.remove_recording_policy_unless_allowed(community):
                self.recording_not_allowed = not self.applicant_datetime_locked
                # 非表示の項目は送信値を使わず、新規は「禁止」、編集は今の値を保つ。
                if not self.instance.pk:
                    self.instance.recording_policy = EventDetail.RecordingPolicy.FORBIDDEN
            elif not self.instance.pk:
                self.initial['recording_policy'] = community.default_recording_policy

        # 既存の記事がある場合（更新時）は自動生成チェックボックスをOFFにする。
        # meta_description だけで判定すると手書きの contents / h1 を上書き再生成してしまう。
        if self.instance and self.instance.pk:
            if self.instance.meta_description or self.instance.contents or self.instance.h1:
                self.fields['generate_blog_article'].initial = False
        # 記事化の同意は新規登録で選び、主催者の編集では今の値を表示する。
        # NG なら生成のチェックボックスを出さず、OK なら自動生成に任せる（保存時の生成と二重にしない）
        configure_article_generation_field(self, self.instance)


    def clean(self):
        cleaned_data = super().clean()
        detail_type = cleaned_data.get('detail_type')
        if detail_type == 'LT':
            for field_name, label in (('theme', 'テーマ'), ('speaker', '発表者')):
                if field_name not in self.errors and not cleaned_data.get(field_name):
                    self.add_error(field_name, f'{label}を入力してください。')
            if not self.instance.pk and 'article_consent' not in self.errors and not cleaned_data.get('article_consent'):
                self.add_error('article_consent', ARTICLE_CONSENT_REQUIRED_MESSAGE)
        elif not self.instance.pk and detail_type in ('SPECIAL', 'BLOG'):
            # 非表示の新規項目は送信値を使わず、既定値で保存する。
            for name in ('x_account', 'article_consent', 'additional_info'):
                self._errors.pop(name, None)
            cleaned_data['article_consent'] = EventDetail._meta.get_field('article_consent').get_default()
            cleaned_data['additional_info'] = ''
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
                cleaned_data['start_time'] = self.instance.event.start_time
                cleaned_data['duration'] = 30

        if is_datetime_locked:
            cleaned_data['start_time'] = self.instance.start_time
            cleaned_data['duration'] = self.instance.duration

        if self.applicant_datetime_locked:
            cleaned_data['start_time'] = self.instance.start_time
            cleaned_data['duration'] = self.instance.duration

        return cleaned_data

    def clean_x_account(self):
        if self.cleaned_data.get('detail_type') == 'LT':
            return normalize_x_account(self.cleaned_data.get('x_account', ''))
        return ''

    def clean_additional_info(self):
        if self.cleaned_data.get('detail_type') != 'LT':
            return ''
        community = self.instance.event.community if self.instance.event_id else None
        return validate_additional_info(self.cleaned_data.get('additional_info', ''), community)

    def save(self, commit=True):
        if (
            not self.instance.pk
            and self.cleaned_data.get('detail_type') == 'LT'
            and self.request
            and self.request.user.is_authenticated
            and self.cleaned_data['speaker'].strip() == self.request.user.display_label
        ):
            user = self.request.user
            self.instance.applicant = user
            with transaction.atomic():
                if commit and user.x_account != self.cleaned_data['x_account']:
                    user.x_account = self.cleaned_data['x_account']
                    user.save(update_fields=['x_account'])
                return super().save(commit=commit)
        return super().save(commit=commit)
