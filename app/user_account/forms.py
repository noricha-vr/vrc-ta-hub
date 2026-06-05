import re

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm
from django.core.validators import FileExtensionValidator

from allauth.socialaccount.forms import SignupForm as SocialSignupForm

from community.models import Community
from community.models import TAGS, PLATFORM_CHOICES, WEEKDAY_CHOICES
from .models import CustomUser
from .vrchat import normalize_vrchat_user_id


X_HANDLE_RE = re.compile(r'^[A-Za-z0-9_]{1,15}\Z')
X_URL_PREFIX_RE = re.compile(r'^https?://(?:www\.)?(?:x|twitter)\.com/', re.IGNORECASE)


def normalize_x_account(value: str) -> str:
    """X のハンドル入力（@付き / URL / 素のハンドル）を素のハンドル名に正規化する。

    空文字はそのまま返す。形式不正は ValueError を投げる。
    """
    if not value:
        return ''
    handle = value.strip()
    handle = X_URL_PREFIX_RE.sub('', handle)
    handle = handle.split('/', 1)[0].split('?', 1)[0]
    if handle.startswith('@'):
        handle = handle[1:]
    if not X_HANDLE_RE.match(handle):
        raise forms.ValidationError(
            'X のハンドル名は英数字とアンダースコアで1〜15文字です（@ や URL も受け付けます）。'
        )
    return handle


class CustomUserCreationForm(UserCreationForm):
    start_time = forms.TimeField(label='開始時刻', initial='22:00',
                                 widget=forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}))
    duration = forms.IntegerField(label='開催時間', help_text='単位は分', initial=60,
                                  widget=forms.NumberInput(attrs={'class': 'form-control'}))
    weekdays = forms.MultipleChoiceField(
        label='曜日',
        choices=WEEKDAY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple
    )
    frequency = forms.CharField(max_length=100, label='開催周期',
                                widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '隔週'}))
    organizers = forms.CharField(max_length=200, label='主催・副主催',
                                 widget=forms.TextInput(attrs={'class': 'form-control'}))
    # assume_scheme='https' は Django 5.0+ で必須化される URLField 引数。
    # 未指定だと Django 6.0 でデフォルトが http→https に変わる旨の警告が出るため明示する
    group_url = forms.URLField(label='VRChatグループURL', required=False, assume_scheme='https',
                               widget=forms.URLInput(attrs={'class': 'form-control',
                                                            'placeholder': 'https://vrc.group/XXXXX'}))
    organizer_url = forms.URLField(label='主催プロフィールURL', required=False, assume_scheme='https',
                                   widget=forms.URLInput(attrs={'class': 'form-control',
                                                                'placeholder': 'https://vrchat.com/home/user/XXXXX'}))
    sns_url = forms.URLField(label='XアカウントURL', required=False, assume_scheme='https',
                             widget=forms.URLInput(attrs={'class': 'form-control',
                                                          'placeholder': 'https://x.com/XXXXX'}),
                             help_text='X以外のSNSのURLも可')
    twitter_hashtag = forms.CharField(max_length=100, label='Xハッシュタグ', required=False,
                                      widget=forms.TextInput(
                                          attrs={'class': 'form-control', 'placeholder': '#VRChat'}), )
    discord = forms.URLField(label='Discordサーバー', required=False, assume_scheme='https',
                             help_text='招待リンクを入力してください。',
                             widget=forms.URLInput(
                                 attrs={'class': 'form-control', 'placeholder': 'https://discord.gg/XXXXXXXXX'}))

    poster_image = forms.ImageField(
        label='ポスター',
        required=True,
        help_text="""最大サイズ: 30MB\n
対応フォーマット: jpg, jpeg, png\n
このサイトやイベント紹介、ワールドに設置されているアセット、APIなどで利用されます""",
        widget=forms.ClearableFileInput(attrs={'class': 'form-control'}),
        validators=[FileExtensionValidator(['jpg', 'jpeg', 'png'])],
        error_messages={
            'required': 'ポスター画像は必須項目です。',
            'invalid': '有効な画像ファイルを選択してください。',
        }
    )
    allow_poster_repost = forms.BooleanField(
        label='集会を紹介するためのポスター転載を許可する',
        required=True,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})

    )

    description = forms.CharField(
        label='イベント紹介',
        widget=forms.Textarea(attrs={'class': 'form-control'}),
        error_messages={
            'required': 'イベント紹介は必須項目です。',
        }
    )
    platform = forms.ChoiceField(
        label='対応プラットフォーム',
        choices=PLATFORM_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'}),
        error_messages={
            'required': '対応プラットフォームは必須項目です。',
        }
    )
    tags = forms.MultipleChoiceField(
        label='タグ',
        choices=TAGS,
        widget=forms.CheckboxSelectMultiple(),
        error_messages={
            'required': '少なくとも1つのタグを選択してください。',
        }
    )

    class Meta(UserCreationForm.Meta):
        model = CustomUser
        fields = ('user_name', 'email', 'password1', 'password2', 'start_time',
                  'duration', 'weekdays', 'frequency', 'organizers', 'group_url', 'organizer_url', 'sns_url',
                  'discord', 'twitter_hashtag', 'poster_image', 'allow_poster_repost', 'description', 'platform',
                  'tags')
        widgets = {
            'user_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'required': 'required'}),
            'password1': forms.PasswordInput(attrs={'class': 'form-control'}),
            'password2': forms.PasswordInput(attrs={'class': 'form-control'}),
            'allow_poster_repost': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        help_texts = {
            'email': 'メールアドレスは公開されません。連絡用として使用します。',
        }
        error_messages = {
            'user_name': {
                'required': 'ユーザー名は必須項目です。',
                'unique': 'このユーザー名は既に使用されています。',
            },
            'email': {
                'required': 'メールアドレスは必須項目です。',
                'invalid': '有効なメールアドレスを入力してください。',
            },
        }

    def save(self, commit=True):
        from community.models import CommunityMember

        user = super().save(commit=False)
        user.user_name = self.cleaned_data['user_name']
        user.display_name = self.cleaned_data['user_name']
        community = Community(
            name=self.cleaned_data['user_name'],
            start_time=self.cleaned_data['start_time'],
            duration=self.cleaned_data['duration'],
            weekdays=self.cleaned_data['weekdays'],
            frequency=self.cleaned_data['frequency'],
            organizers=self.cleaned_data['organizers'],
            group_url=self.cleaned_data['group_url'],
            organizer_url=self.cleaned_data['organizer_url'],
            sns_url=self.cleaned_data['sns_url'],
            discord=self.cleaned_data['discord'],
            twitter_hashtag=self.cleaned_data['twitter_hashtag'],
            poster_image=self.cleaned_data['poster_image'],
            description=self.cleaned_data['description'],
            platform=self.cleaned_data['platform'],
            tags=self.cleaned_data['tags'],
            allow_poster_repost=self.cleaned_data.get('allow_poster_repost', False)
        )
        if commit:
            user.save()
            community.save()
            # オーナーとしてCommunityMemberを作成
            CommunityMember.objects.create(
                community=community,
                user=user,
                role=CommunityMember.Role.OWNER
            )
        return user


class BootstrapAuthenticationForm(AuthenticationForm):
    """カスタムユーザーモデルのuser_nameフィールドに対応した認証フォーム."""

    username = forms.CharField(
        label='ユーザー名',
        widget=forms.TextInput(attrs={'class': 'form-control', 'autofocus': True}),
    )
    remember = forms.BooleanField(
        label='ログインしたままにする',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # チェックボックス以外のフィールドにform-controlを適用
        for name, field in self.fields.items():
            if name != 'remember':
                field.widget.attrs.update({'class': 'form-control'})

    def clean(self):
        username = self.cleaned_data.get('username')
        password = self.cleaned_data.get('password')

        if username is not None and password:
            # user_nameフィールドでユーザーを検索して認証
            self.user_cache = self.authenticate_user(username, password)
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            else:
                self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data

    def authenticate_user(self, username, password):
        """user_nameフィールドを使用してユーザーを認証."""
        from django.contrib.auth import authenticate

        # Djangoの認証バックエンドはUSERNAME_FIELDを使用するため、
        # usernameパラメータとして渡す（内部でuser_nameとして処理される）
        return authenticate(self.request, username=username, password=password)


class BootstrapPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})


class LocalSignupForm(UserCreationForm):
    """Discord 未設定時に使うローカルサインアップフォーム."""

    email = forms.EmailField(
        label='メールアドレス',
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control'}),
        help_text='メールアドレスは公開されません。連絡用として使用します。',
    )

    class Meta(UserCreationForm.Meta):
        model = CustomUser
        fields = ('user_name', 'email', 'password1', 'password2')
        widgets = {
            'user_name': forms.TextInput(attrs={'class': 'form-control'}),
        }
        help_texts = {
            'user_name': 'ログインに使用する一意のユーザー名です。',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user_name'].label = 'ログインユーザー名'
        self.fields['password1'].label = 'パスワード'
        self.fields['password2'].label = 'パスワード（確認）'
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('このメールアドレスは既に登録されています。')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        if not user.display_name:
            user.display_name = user.user_name
        if commit:
            user.save()
        return user


class CustomUserChangeForm(forms.ModelForm):
    # 入力時には @ や URL を受け付けるため、フィールド側の max_length は緩める
    # （保存時には clean_x_account で正規化済みハンドル名のみがモデルへ渡る）
    x_account = forms.CharField(
        label='X (Twitter) アカウント',
        max_length=64,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'noricha_vr',
            'autocomplete': 'off',
        }),
        help_text='任意。@ や https://x.com/ のURLで入力しても自動でハンドル名に正規化されます。',
    )
    vrchat_user_id = forms.CharField(
        label='VRChatユーザーID',
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'https://vrchat.com/home/user/usr_...',
            'autocomplete': 'off',
        }),
        help_text='任意。VRChatのプロフィールURLで入力してもユーザーIDに正規化されます。',
    )

    class Meta:
        model = CustomUser
        fields = ('display_name', 'user_name', 'email', 'x_account', 'vrchat_user_id')
        widgets = {
            'display_name': forms.TextInput(attrs={'class': 'form-control'}),
            'user_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.TextInput(attrs={'class': 'form-control'}),
        }
        labels = {
            'display_name': '表示名',
            'user_name': 'ログインユーザー名',
        }
        help_texts = {
            'display_name': 'VRChat内の名前や発表者名として表示されます。同じ表示名を複数ユーザーが使用できます。',
            'user_name': 'ログインと内部識別に使用する一意のユーザー名です。通常は変更不要です。',
        }

    def clean_x_account(self):
        return normalize_x_account(self.cleaned_data.get('x_account', ''))

    def clean_vrchat_user_id(self):
        return normalize_vrchat_user_id(self.cleaned_data.get('vrchat_user_id', ''))


class SocialAccountDisconnectForm(forms.Form):
    """Discord 連携解除の確認用フォーム。パスワード一致を検証する。"""

    password = forms.CharField(
        label='現在のパスワード',
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'current-password',
            'class': 'form-control',
        }),
        help_text='連携解除後にメールアドレスでログインできることを確認するため、現在のパスワードを入力してください。',
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password(self):
        password = self.cleaned_data['password']
        if not self.user.check_password(password):
            raise forms.ValidationError('パスワードが正しくありません。')
        return password


class CustomSocialSignupForm(SocialSignupForm):
    """Discord OAuth認証後のサインアップフォームにBootstrapのスタイルを適用."""

    # ユーザー名フィールドの最大文字数
    USER_NAME_MAX_LENGTH = 150

    user_name = forms.CharField(
        label='ログインユーザー名',
        max_length=USER_NAME_MAX_LENGTH,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        help_text='ログインに使用する一意のユーザー名です。',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})
        # メールアドレスの設定
        if 'email' in self.fields:
            self.fields['email'].required = True
            self.fields['email'].label = 'メールアドレス'

        # Discordユーザー名をプレースホルダーに設定
        if hasattr(self, 'sociallogin') and self.sociallogin:
            discord_username = self.sociallogin.account.extra_data.get('username', '')
            if discord_username:
                self.fields['user_name'].widget.attrs['placeholder'] = discord_username

        # フィールド順序を設定
        self.order_fields(['user_name', 'email'])

    def clean_email(self):
        """メールアドレスの重複チェック.

        大文字小文字を区別せずに重複をチェックする。
        """
        email = self.cleaned_data.get('email')
        if email and CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                'このメールアドレスは既に登録されています。'
                '既存のアカウントにログインしてから、Discord連携を行ってください。'
            )
        return email

    def clean_user_name(self):
        """ユーザー名の重複チェック."""
        user_name = self.cleaned_data.get('user_name')
        if user_name and CustomUser.objects.filter(user_name=user_name).exists():
            raise forms.ValidationError(
                'このユーザー名は既に使用されています。別のユーザー名を入力してください。'
            )
        return user_name
