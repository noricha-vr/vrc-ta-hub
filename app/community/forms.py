from django import forms
from django.core.validators import MaxLengthValidator
from django.forms.widgets import CheckboxSelectMultiple


from .constants import (
    WEEKDAY_CHOICES, RECORDING_POLICY_FORM_CHOICES, recording_policy_admin_choices,
    RECORDING_ALLOWED_LABEL, DEFAULT_RECORDING_POLICY_LABEL, DEFAULT_RECORDING_POLICY_ADMIN_HELP_TEXT,
    MAX_LT_DURATION_MINUTES, MAX_LT_START_OFFSET_MINUTES,
)
from .recording_settings import _parse_recording_allowed, _resolve_default_recording_policy
from .libs import extract_vrc_group_short_code, normalize_vrc_group_short_code, resolve_vrc_group_url
from .models import Community, TAGS, FORM_TAGS

POSTER_REQUIREMENTS_HELP_TEXT = (
    "推奨サイズ: A4比率・縦4096px。"
    "高解像度の原本を保持し、表示時に自動で最適サイズに変換されます。"
)


ALLOWED_GROUP_URL_DOMAINS = {"vrc.group", "vrchat.com"}


class VrcGroupUrlMixin:
    """vrc.group 短縮URLを正規URLに変換し、許可ドメインのみ受け付ける。"""

    # 短縮URL (例: https://vrc.group/GAME.2515 = 25文字) の貼り付けを clean前に通すため広げる
    GROUP_SHORT_CODE_INPUT_MAX_LENGTH = 200

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields.get('group_short_code')
        if field is not None:
            field.max_length = self.GROUP_SHORT_CODE_INPUT_MAX_LENGTH
            field.validators = [
                v for v in field.validators if not isinstance(v, MaxLengthValidator)
            ] + [MaxLengthValidator(self.GROUP_SHORT_CODE_INPUT_MAX_LENGTH)]
            field.widget.attrs['maxlength'] = str(self.GROUP_SHORT_CODE_INPUT_MAX_LENGTH)

    def clean_group_url(self):
        self._extracted_group_short_code = ''
        url = self.cleaned_data.get('group_url', '')
        if not url:
            return url
        from urllib.parse import urlparse
        parsed = urlparse(url)
        hostname = (parsed.hostname or '').lower()
        if not any(domain in hostname for domain in ALLOWED_GROUP_URL_DOMAINS):
            raise forms.ValidationError(
                "VRChatグループのURL（vrchat.com または vrc.group）を入力してください。"
            )
        self._extracted_group_short_code = extract_vrc_group_short_code(url)
        return resolve_vrc_group_url(url)

    def clean_group_short_code(self):
        value = self.cleaned_data.get('group_short_code', '')
        try:
            return normalize_vrc_group_short_code(value)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean(self):
        cleaned = super().clean()
        if (
            'group_short_code' in self.fields
            and not cleaned.get('group_short_code')
            and getattr(self, '_extracted_group_short_code', '')
        ):
            cleaned['group_short_code'] = self._extracted_group_short_code
        return cleaned


class CommunitySearchForm(forms.Form):
    query = forms.CharField(
        label='検索',
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'キーワードを入力',
            'class': 'form-control',
        })
    )
    weekdays = forms.MultipleChoiceField(
        label='曜日',
        choices=WEEKDAY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple
    )
    tags = forms.MultipleChoiceField(
        label='タグ',
        choices=TAGS,
        required=False,
        widget=forms.CheckboxSelectMultiple
    )


class CommunityForm(VrcGroupUrlMixin, forms.ModelForm):
    weekdays = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES,
        widget=CheckboxSelectMultiple(),
        required=False,
    )
    tags = forms.MultipleChoiceField(  # tags フィールドを追加（admin用なので全タグ）
        choices=TAGS,
        widget=CheckboxSelectMultiple(),
        required=False,
    )
    allow_poster_repost = forms.BooleanField(
        label='集会を紹介するためのポスター転載を許可する',
        required=False,
    )

    class Meta:
        model = Community
        fields = '__all__'
        # 追加: BooleanFieldもフォームで扱えるようにする
        widgets = {
            'allow_poster_repost': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'recording_allowed' in self.fields:
            self.fields['recording_allowed'].label = RECORDING_ALLOWED_LABEL
            self.fields['recording_allowed'].help_text = (
                'オンにすると、この集会の発表がハブの自動撮影ボットの対象になります'
                '（オフの場合は発表申請も「許可しない」で受け付けます。'
                '集会独自の撮影や写真撮影を制限する設定ではありません）。'
            )
        if 'default_recording_policy' in self.fields:
            current_policy = (
                self.instance.default_recording_policy
                if self.instance and self.instance.pk
                else None
            )
            self.fields['default_recording_policy'].label = DEFAULT_RECORDING_POLICY_LABEL
            self.fields['default_recording_policy'].help_text = DEFAULT_RECORDING_POLICY_ADMIN_HELP_TEXT
            self.fields['default_recording_policy'].choices = recording_policy_admin_choices(current_policy)
        if self.instance and self.instance.pk:
            self.initial['weekdays'] = self.instance.weekdays
            self.initial['tags'] = self.instance.tags  # tags の初期値を設定

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.weekdays = self.cleaned_data['weekdays']
        instance.tags = self.cleaned_data['tags']  # tags を保存
        if commit:
            instance.save()
        return instance


class CommunityUpdateForm(VrcGroupUrlMixin, forms.ModelForm):
    weekdays = forms.MultipleChoiceField(
        label='曜日',
        choices=WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-inline'}),
        required=False
    )

    tags = forms.MultipleChoiceField(
        label='タグ',
        choices=FORM_TAGS,
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-inline'}),
        required=False
    )

    class Meta:
        model = Community
        fields = [
            'name', 'start_time', 'duration', 'weekdays', 'frequency', 'organizers',
            'group_url', 'group_short_code', 'organizer_url', 'sns_url', 'discord', 'twitter_hashtag',
            'poster_image', 'allow_poster_repost',
            'description', 'platform', 'tags',
        ]
        labels = {
            'sns_url': 'XアカウントURL',
            'twitter_hashtag': 'Xハッシュタグ',
        }
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'start_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'duration': forms.NumberInput(attrs={'class': 'form-control'}),
            'frequency': forms.TextInput(attrs={'class': 'form-control'}),
            'organizers': forms.TextInput(attrs={'class': 'form-control'}),
            'group_url': forms.URLInput(attrs={'class': 'form-control'}),
            'group_short_code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'GAME.2515'}),
            'organizer_url': forms.URLInput(attrs={'class': 'form-control'}),
            'sns_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://x.com/XXXXX'}),
            'discord': forms.URLInput(attrs={'class': 'form-control'}),
            'twitter_hashtag': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '#VRChat'}),
            'poster_image': forms.FileInput(attrs={'class': 'form-control-file'}),
            'allow_poster_repost': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'description': forms.Textarea(attrs={'class': 'form-control'}),
            'platform': forms.Select(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['weekdays'].initial = self.instance.weekdays
            self.fields['tags'].initial = self.instance.tags
        self.fields['poster_image'].help_text = POSTER_REQUIREMENTS_HELP_TEXT


class CommunityReopenForm(CommunityUpdateForm):
    """再開時も管理者専用タグを維持し、編集可能なタグだけを表示する。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        editable_tags = {value for value, _ in FORM_TAGS}
        self.initial['tags'] = [tag for tag in self.instance.tags if tag in editable_tags]
        self.fields['tags'].initial = self.initial['tags']
        self.fields.pop('weekdays', None)
        self.fields['tags'].widget.attrs.pop('class', None)

    class Meta(CommunityUpdateForm.Meta):
        fields = [name for name in CommunityUpdateForm.Meta.fields
                  if name not in ('frequency', 'weekdays')]

    def clean_tags(self):
        editable_tags = {value for value, _ in FORM_TAGS}
        preserved = [tag for tag in self.instance.tags if tag not in editable_tags]
        return self.cleaned_data['tags'] + preserved


class CommunityCreateForm(VrcGroupUrlMixin, forms.ModelForm):
    """集会新規登録用フォーム."""

    # 同意対象は公開文書として実在する「掲載・審査基準」に限定する
    # （独立したガイドライン文書は存在しないため、読めないものへの同意を求めない）
    guidelines_agreed = forms.BooleanField(
        label='掲載・審査基準を確認し、内容に同意します。',
        required=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        error_messages={
            'required': '登録するには、掲載・審査基準への同意が必要です。',
        },
    )

    weekdays = forms.MultipleChoiceField(
        label='曜日',
        choices=WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-inline'}),
        required=False
    )

    tags = forms.MultipleChoiceField(
        label='タグ',
        choices=FORM_TAGS,
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-inline'}),
        required=True,
        error_messages={
            'required': '少なくとも1つのタグを選択してください。',
        }
    )

    accepts_lt_application = forms.TypedChoiceField(
        label='発表申請の受付',
        choices=[('on', '受け付ける'), ('off', '受け付けない')],
        coerce=lambda value: value == 'on',
        empty_value=True,
        required=False,
        initial='on',
    )
    recording_allowed = forms.CharField(
        label=RECORDING_ALLOWED_LABEL,
        required=False,
        strip=False,
        initial='false',
        widget=forms.RadioSelect(choices=[('true', '許可する'), ('false', '許可しない')]),
        help_text=(
            '「許可しない」場合はハブの自動撮影ボットの対象にならず、'
            '発表申請も「許可しない」で受け付けます'
            '（集会独自の撮影や写真撮影を制限する設定ではありません）。'
        ),
    )
    default_recording_policy = forms.CharField(
        label=DEFAULT_RECORDING_POLICY_LABEL,
        required=False,
        strip=False,
        widget=forms.RadioSelect(choices=RECORDING_POLICY_FORM_CHOICES),
        help_text='発表申請フォームの初期値です。登壇者は申請時に変えられます。',
    )
    default_recording_policy_chosen = forms.CharField(required=False, strip=False, widget=forms.HiddenInput)
    default_lt_duration = forms.IntegerField(
        label='発表の持ち時間（分）', required=False, initial=30, min_value=1, max_value=MAX_LT_DURATION_MINUTES,
        help_text='発表と質疑応答を含めた合計時間（分）。',
    )
    lt_start_offset_minutes = forms.IntegerField(
        label='最初の発表を始めるタイミング（集会開始から何分後）',
        required=False, initial=30, min_value=0, max_value=MAX_LT_START_OFFSET_MINUTES,
        help_text='例：集会開始が22:00の場合、15分後なら発表開始は22:15です。',
    )

    def clean(self):
        cleaned = super().clean()
        allowed = _parse_recording_allowed(cleaned.get('recording_allowed'))
        cleaned['recording_allowed'] = allowed
        cleaned['default_recording_policy'] = _resolve_default_recording_policy(
            cleaned.get('default_recording_policy'), self.instance.default_recording_policy,
            was_allowed=self.instance.recording_allowed, allowed=allowed,
            chosen=cleaned.get('default_recording_policy_chosen') == '1',
        )
        for name in ('default_lt_duration', 'lt_start_offset_minutes'):
            if name in cleaned and cleaned[name] is None:
                cleaned[name] = Community._meta.get_field(name).get_default()
        return cleaned

    class Meta:
        model = Community
        fields = [
            'name', 'start_time', 'duration', 'weekdays', 'frequency', 'organizers',
            'group_url', 'group_short_code', 'organizer_url', 'sns_url', 'discord', 'twitter_hashtag',
            'poster_image', 'allow_poster_repost', 'description', 'platform', 'tags',
            'accepts_lt_application', 'recording_allowed', 'default_recording_policy',
            'default_lt_duration', 'lt_start_offset_minutes'
        ]
        labels = {
            'sns_url': 'XアカウントURL',
            'twitter_hashtag': 'Xハッシュタグ',
        }
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'start_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'duration': forms.NumberInput(attrs={'class': 'form-control'}),
            'frequency': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '隔週'}),
            'organizers': forms.TextInput(attrs={'class': 'form-control'}),
            'group_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://vrc.group/XXXXX'}),
            'group_short_code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'GAME.2515'}),
            'organizer_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://vrchat.com/home/user/XXXXX'}),
            'sns_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://x.com/XXXXX'}),
            'discord': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://discord.gg/XXXXXXXXX'}),
            'twitter_hashtag': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '#VRChat'}),
            'poster_image': forms.FileInput(attrs={'class': 'form-control-file'}),
            'allow_poster_repost': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'description': forms.Textarea(attrs={'class': 'form-control'}),
            'platform': forms.Select(attrs={'class': 'form-control'}),
        }
        error_messages = {
            'poster_image': {
                'required': 'ポスター画像は必須項目です。',
            },
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['accepts_lt_application'] = 'on' if self.instance.accepts_lt_application else 'off'
        self.initial['recording_allowed'] = 'true' if self.instance.recording_allowed else 'false'
        self.initial['default_recording_policy'] = self.instance.default_recording_policy
        self.fields['poster_image'].required = True
        self.fields['poster_image'].help_text = (
            "最大サイズ: 30MB。"
            "対応フォーマット: jpg, jpeg, png。"
            "このサイトやイベント紹介、ワールドに設置されているアセット、APIなどで利用されます。"
            f"{POSTER_REQUIREMENTS_HELP_TEXT}"
        )
        self.fields['allow_poster_repost'].initial = True
