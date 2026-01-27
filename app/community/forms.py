from django import forms
from django.forms.widgets import CheckboxSelectMultiple

from .models import Community, WEEKDAY_CHOICES, TAGS, FORM_TAGS


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


class CommunityForm(forms.ModelForm):
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


class CommunityUpdateForm(forms.ModelForm):
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
            'group_url', 'organizer_url', 'sns_url', 'discord', 'twitter_hashtag',
            'poster_image', 'allow_poster_repost', 'description', 'platform', 'tags',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'start_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'duration': forms.NumberInput(attrs={'class': 'form-control'}),
            'frequency': forms.TextInput(attrs={'class': 'form-control'}),
            'organizers': forms.TextInput(attrs={'class': 'form-control'}),
            'group_url': forms.URLInput(attrs={'class': 'form-control'}),
            'organizer_url': forms.URLInput(attrs={'class': 'form-control'}),
            'sns_url': forms.URLInput(attrs={'class': 'form-control'}),
            'discord': forms.URLInput(attrs={'class': 'form-control'}),
            'twitter_hashtag': forms.TextInput(attrs={'class': 'form-control'}),
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


class CommunityCreateForm(forms.ModelForm):
    """集会新規登録用フォーム."""

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

    class Meta:
        model = Community
        fields = [
            'name', 'start_time', 'duration', 'weekdays', 'frequency', 'organizers',
            'group_url', 'organizer_url', 'sns_url', 'discord', 'twitter_hashtag',
            'poster_image', 'allow_poster_repost', 'description', 'platform', 'tags'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'start_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'duration': forms.NumberInput(attrs={'class': 'form-control'}),
            'frequency': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '隔週'}),
            'organizers': forms.TextInput(attrs={'class': 'form-control'}),
            'group_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://vrc.group/XXXXX'}),
            'organizer_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://vrchat.com/home/user/XXXXX'}),
            'sns_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://twitter.com/XXXXX'}),
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
        self.fields['poster_image'].required = True
        self.fields['poster_image'].help_text = """最大サイズ: 30MB
対応フォーマット: jpg, jpeg, png
このサイトやイベント紹介、ワールドに設置されているアセット、APIなどで利用されます"""
        self.fields['allow_poster_repost'].initial = True
