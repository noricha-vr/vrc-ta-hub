"""LT (発表) 申請関連フォーム。"""

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from user_account.forms import normalize_x_account
from ..models import EventDetail, Event
from ..thumbnail import SLIDE_THUMBNAIL_ASPECT_RATIO_TEXT
from .mixins import EventDetailMediaFormMixin


# LT申請時のデフォルト発表時間（分）
DEFAULT_LT_DURATION = 15


class LTApplicationEditForm(EventDetailMediaFormMixin, forms.ModelForm):
    """LT申請者が自分の申請内容を編集するフォーム"""

    generate_blog_article = forms.BooleanField(
        label='記事を自動生成する',
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input form-check-input-lg'}),
        help_text='PDFまたはYouTube URLが設定されている場合、AIによって記事を自動生成します'
    )

    class Meta:
        model = EventDetail
        fields = ['theme', 'speaker', 'slide_file', 'slide_url', 'thumbnail_image', 'youtube_url', 'h1', 'contents',
                  'generate_blog_article']
        widgets = {
            'theme': forms.TextInput(attrs={'class': 'form-control'}),
            'speaker': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'VRChat表示名を入力'
            }),
            'slide_url': forms.URLInput(attrs={'class': 'form-control'}),
            'slide_file': forms.ClearableFileInput(attrs={'class': 'form-control-file', 'accept': '.pdf'}),
            'thumbnail_image': forms.ClearableFileInput(attrs={'class': 'form-control-file', 'accept': 'image/*'}),
            'youtube_url': forms.URLInput(attrs={'class': 'form-control'}),
            'h1': forms.TextInput(attrs={'class': 'form-control'}),
            'contents': forms.Textarea(attrs={'class': 'form-control', 'rows': '8'}),
        }
        help_texts = {
            'contents': '※ Markdown形式で記述してください。',
            'h1': '※ 空のときはテーマが使われます。',
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 記事未生成ならON、生成済みならOFF
        has_article = self.instance and self.instance.pk and self.instance.meta_description
        self.initial['generate_blog_article'] = not has_article


class LTApplicationForm(forms.Form):
    """LT発表の申請フォーム"""

    event = forms.ModelChoiceField(
        queryset=Event.objects.none(),
        label='開催日',
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text='発表したい開催日を選択してください'
    )

    theme = forms.CharField(
        label='テーマ',
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '発表テーマを入力してください'
        }),
        help_text='発表内容を簡潔に表すテーマを入力してください'
    )

    speaker = forms.CharField(
        label='発表者名',
        max_length=200,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'VRChat表示名を入力してください'
        }),
        help_text='VRChatの表示名を入力してください。送信するとアカウントの表示名としても保存されます。'
    )

    duration = forms.IntegerField(
        label='発表時間（分）',
        initial=DEFAULT_LT_DURATION,
        min_value=5,
        max_value=60,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        help_text='希望する発表時間を分単位で入力してください（5〜60分）'
    )

    x_account = forms.CharField(
        label='X (Twitter) アカウント',
        max_length=64,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'noricha_vr',
            'autocomplete': 'off',
        }),
        help_text='任意。@ や https://x.com/ のURLでもOK。アカウント情報にも保存されます。'
    )

    additional_info = forms.CharField(
        label='追加情報',
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 6,
        }),
        help_text='主催者が設定したテンプレートに沿って入力してください'
    )

    def __init__(self, *args, **kwargs):
        self.community = kwargs.pop('community', None)
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        if self.community:
            # コミュニティのデフォルトLT発表時間を設定
            self.fields['duration'].initial = self.community.default_lt_duration

            # accepts_lt_application=True かつ未来のイベントのみ
            today = timezone.now().date()
            self.fields['event'].queryset = Event.objects.filter(
                community=self.community,
                accepts_lt_application=True,
                date__gte=today
            ).order_by('date', 'start_time')

            # テンプレートが設定されている場合は初期値として編集可能にする
            if self.community.lt_application_template:
                self.fields['additional_info'].initial = self.community.lt_application_template
            else:
                self.fields['additional_info'].help_text = (
                    '追加で伝えたい情報があれば入力してください'
                )

        if self.user and self.user.is_authenticated:
            self.fields['speaker'].initial = self.user.display_label
            self.fields['x_account'].initial = self.user.x_account

    def clean_speaker(self):
        """発表者名を EventDetail.speaker と user.display_name 用に検証する。"""
        speaker = (self.cleaned_data.get('speaker') or '').strip()
        if not speaker:
            raise ValidationError('発表者名を入力してください')
        if len(speaker) > EventDetail._meta.get_field('speaker').max_length:
            raise ValidationError('発表者名は200文字以下で入力してください')
        return speaker

    def clean_x_account(self):
        return normalize_x_account(self.cleaned_data.get('x_account', ''))

    def clean_additional_info(self):
        """追加情報のバリデーション"""
        additional_info = self.cleaned_data.get('additional_info', '')

        if not self.community or not self.community.lt_application_template:
            return additional_info

        template = self.community.lt_application_template

        # テンプレートと同一内容のチェック
        if additional_info.strip() == template.strip():
            raise ValidationError('テンプレートの各項目を入力してください')

        return additional_info


class LTApplicationReviewForm(forms.Form):
    """LT申請の承認/却下フォーム"""

    ACTION_CHOICES = [
        ('approve', '承認する'),
        ('reject', '却下する'),
    ]

    action = forms.ChoiceField(
        choices=ACTION_CHOICES,
        label='アクション',
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
        initial='approve'
    )

    rejection_reason = forms.CharField(
        label='却下理由',
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': '却下する場合は理由を入力してください'
        }),
        help_text='却下する場合は、申請者に伝える理由を入力してください'
    )

    def clean(self):
        cleaned_data = super().clean()
        action = cleaned_data.get('action')
        rejection_reason = cleaned_data.get('rejection_reason')

        if action == 'reject' and not rejection_reason:
            raise ValidationError('却下する場合は理由を入力してください')

        return cleaned_data
