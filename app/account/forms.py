from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm
from django.core.validators import FileExtensionValidator

from community.models import Community
from community.models import TAGS, PLATFORM_CHOICES, WEEKDAY_CHOICES
from .models import CustomUser


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
    group_url = forms.URLField(label='VRChatグループURL', required=False,
                               widget=forms.URLInput(attrs={'class': 'form-control',
                                                            'placeholder': 'https://vrc.group/XXXXX'}))
    organizer_url = forms.URLField(label='主催プロフィールURL', required=False,
                                   widget=forms.URLInput(attrs={'class': 'form-control',
                                                                'placeholder': 'https://vrchat.com/home/user/XXXXX'}))
    sns_url = forms.URLField(label='TwitterURL', required=False, widget=forms.URLInput(attrs={'class': 'form-control'
        , 'placeholder': 'https://twitter.com/XXXXX'}),
                             help_text='Twitter以外のSNSのURLも可')
    twitter_hashtag = forms.CharField(max_length=100, label='Twitterハッシュタグ', required=False,
                                      widget=forms.TextInput(
                                          attrs={'class': 'form-control', 'placeholder': '#VRChat'}), )
    discord = forms.URLField(label='Discordサーバー', required=False,
                             help_text='招待リンクを入力してください。',
                             widget=forms.URLInput(
                                 attrs={'class': 'form-control', 'placeholder': 'https://discord.gg/XXXXXXXXX'}))

    poster_image = forms.ImageField(
        label='ポスター',
        required=False,
        help_text='最大サイズ: 30MB, 対応フォーマット: jpg, jpeg, png',
        widget=forms.ClearableFileInput(attrs={'class': 'form-control'}),
        validators=[FileExtensionValidator(['jpg', 'jpeg', 'png'])],
    )
    description = forms.CharField(label='イベント紹介', widget=forms.Textarea(attrs={'class': 'form-control'}))
    platform = forms.ChoiceField(label='対応プラットフォーム', choices=PLATFORM_CHOICES,
                                 widget=forms.Select(attrs={'class': 'form-control'}))
    tags = forms.MultipleChoiceField(label='タグ', choices=TAGS,
                                     widget=forms.CheckboxSelectMultiple())

    class Meta(UserCreationForm.Meta):
        model = CustomUser
        fields = ('user_name', 'email', 'discord_id', 'password1', 'password2', 'start_time',
                  'duration', 'weekdays', 'frequency', 'organizers', 'group_url', 'organizer_url', 'sns_url',
                  'discord', 'twitter_hashtag', 'poster_image', 'description', 'platform', 'tags')
        widgets = {
            'user_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'required': 'required'}),
            'discord_id': forms.TextInput(attrs={'class': 'form-control', 'required': 'required'}),
            'password1': forms.PasswordInput(attrs={'class': 'form-control'}),
            'password2': forms.PasswordInput(attrs={'class': 'form-control'}),
        }
        help_texts = {
            'email': 'メールアドレスは公開されません。連絡用として使用します。',
            'discord_id': 'ディスコードIDは公開されません。イベント開催日程の調整やお知らせなどのためにDiscordサーバーに招待します。'
        }

    def save(self, commit=True):
        user = super().save(commit=False)
        user.user_name = self.cleaned_data['user_name']
        community = Community(
            custom_user=user,
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
            tags=self.cleaned_data['tags']
        )
        if commit:
            user.save()
            community.save()
        return user


class BootstrapAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})


class BootstrapPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})


class CustomUserChangeForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ('user_name', 'email', 'discord_id')
        widgets = {
            'user_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.TextInput(attrs={'class': 'form-control'}),
            'discord_id': forms.TextInput(attrs={'class': 'form-control'}),
        }
