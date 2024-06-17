from community.models import Community, TAGS, PLATFORM_CHOICES, WEEKDAY_CHOICES

from django import forms
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm
from .models import CustomUser
from community.models import Community
from django.core.validators import FileExtensionValidator


class CustomUserCreationForm(UserCreationForm):
    start_time = forms.TimeField(label='開始時刻',
                                 widget=forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}))
    duration = forms.IntegerField(label='開催時間', help_text='単位は分',
                                  widget=forms.NumberInput(attrs={'class': 'form-control'}))
    weekdays = forms.MultipleChoiceField(
        label='曜日',
        choices=WEEKDAY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple
    )
    frequency = forms.CharField(max_length=100, label='開催周期',
                                widget=forms.TextInput(attrs={'class': 'form-control'}))
    organizers = forms.CharField(max_length=200, label='主催・副主催',
                                 widget=forms.TextInput(attrs={'class': 'form-control'}))
    group_url = forms.URLField(label='VRChatグループURL', required=False,
                               widget=forms.URLInput(attrs={'class': 'form-control'}))
    organizer_url = forms.URLField(label='主催プロフィールURL', required=False,
                                   widget=forms.URLInput(attrs={'class': 'form-control'}))
    sns_url = forms.URLField(label='SNS', required=False, widget=forms.URLInput(attrs={'class': 'form-control'}))
    discord = forms.URLField(label='Discord', required=False, widget=forms.URLInput(attrs={'class': 'form-control'}))
    twitter_hashtag = forms.CharField(max_length=100, label='Twitterハッシュタグ', required=False,
                                      widget=forms.TextInput(attrs={'class': 'form-control'}))

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
        fields = ('user_name', 'email', 'password1', 'password2', 'start_time',
                  'duration', 'weekdays', 'frequency', 'organizers', 'group_url', 'organizer_url', 'sns_url',
                  'discord', 'twitter_hashtag', 'poster_image', 'description', 'platform', 'tags')
        widgets = {
            'user_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'password1': forms.PasswordInput(attrs={'class': 'form-control'}),
            'password2': forms.PasswordInput(attrs={'class': 'form-control'}),
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


from django import forms
from .models import CustomUser

from django import forms
from django.contrib.auth.forms import AuthenticationForm


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
        fields = ('user_name', 'email')
        widgets = {
            'user_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.TextInput(attrs={'class': 'form-control'}),
        }
