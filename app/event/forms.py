from datetime import datetime, timedelta
from django import forms
from django.core.exceptions import ValidationError

from community.models import WEEKDAY_CHOICES, TAGS, Community
from .models import EventDetail


class EventSearchForm(forms.Form):
    name = forms.CharField(
        label='集会名',
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '集会名を入力',
        })
    )
    weekday = forms.MultipleChoiceField(
        label='曜日',
        choices=WEEKDAY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple()
    )
    tags = forms.MultipleChoiceField(
        label='タグ',
        choices=TAGS,
        required=False,
        widget=forms.CheckboxSelectMultiple()
    )


from django import forms
from django.utils import timezone
from .models import Event


class EventCreateForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = ['date', 'start_time', 'duration']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'start_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'duration': forms.NumberInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)  # requestオブジェクトを受け取る
        super().__init__(*args, **kwargs)
        if self.request and self.request.user.is_authenticated:
            community = Community.objects.filter(custom_user=self.request.user).first()
            self.fields['start_time'].initial = community.start_time  # Communityから初期値を設定
            self.fields['duration'].initial = community.duration  # Communityから初期値を設定

    def clean(self):
        cleaned_data = super().clean()
        # 過去日付のバリデーションを解除（何もしない）
        return cleaned_data


class EventDetailForm(forms.ModelForm):
    start_time = forms.TimeField(
        label='開始時間',
        widget=forms.TimeInput(
            attrs={'type': 'time', 'class': 'form-control w-25'})  # Bootstrapのクラスを追加
    )
    duration = forms.IntegerField(
        label='発表時間（分）',
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

    class Meta:
        model = EventDetail
        fields = ['detail_type', 'theme', 'speaker', 'start_time', 'duration', 'slide_url', 'slide_file', 'youtube_url', 'h1',
                  'contents']
        widgets = {
            'detail_type': forms.RadioSelect(attrs={'class': 'form-check-input'}),
            'youtube_url': forms.URLInput(attrs={'class': 'form-control'}),
            'slide_url': forms.URLInput(attrs={'class': 'form-control'}),
            'slide_file': forms.ClearableFileInput(attrs={'class': 'form-control-file', 'accept': '.pdf'}),
            'speaker': forms.TextInput(attrs={'class': 'form-control'}),
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
            'slide_url': '外部のスライドシステムのURLや、参考ページのURLを入力してください。',
            'slide_file': '※ PDFファイルのみアップロード可能です（最大30MB）。',
        }

    # start_time と duration の初期値はEventCreateFormと同じにする
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        if self.request and self.request.user.is_authenticated:
            community = Community.objects.filter(custom_user=self.request.user).first()
            self.fields['start_time'].initial = community.start_time
            self.fields['duration'].initial = 30

    def clean(self):
        cleaned_data = super().clean()
        detail_type = cleaned_data.get('detail_type')
        
        # 特別企画とブログの場合、非表示フィールドにデフォルト値を設定
        if detail_type == 'SPECIAL':
            # 特別企画のデフォルト値
            cleaned_data['theme'] = 'Special Event'
            cleaned_data['speaker'] = ''
            # start_timeは入力されたものを使用
            cleaned_data['duration'] = 60
        elif detail_type == 'BLOG':
            # ブログのデフォルト値
            cleaned_data['theme'] = 'Blog'
            cleaned_data['speaker'] = ''
            cleaned_data['start_time'] = self.instance.event.start_time if self.instance.pk else self.fields['start_time'].initial
            cleaned_data['duration'] = 30
        
        return cleaned_data
    
    def clean_slide_file(self):
        slide_file = self.cleaned_data.get('slide_file')
        if slide_file:
            # ファイル形式チェック（PDFのみ許可）
            if not slide_file.name.lower().endswith('.pdf'):
                raise ValidationError('PDFファイルのみアップロード可能です。')
            
            # ファイルサイズチェック
            if slide_file.size > 30 * 1024 * 1024:  # 30MB
                raise ValidationError('ファイルサイズが30MBを超えています。')
        return slide_file


RECURRENCE_CHOICES = [
    ('none', '単発イベント'),
    ('weekly', '毎週'),
    ('biweekly', '隔週'),
    ('monthly_by_day', '毎月同じ曜日（例：第2月曜日）'),
    ('monthly_by_date', '毎月同じ日（例：毎月15日）'),
]

CALENDAR_WEEKDAY_CHOICES = [
    ('MO', '月曜日'),
    ('TU', '火曜日'),
    ('WE', '水曜日'),
    ('TH', '木曜日'),
    ('FR', '金曜日'),
    ('SA', '土曜日'),
    ('SU', '日曜日'),
]

WEEK_NUMBER_CHOICES = [
    (1, '第1'),
    (2, '第2'),
    (3, '第3'),
    (4, '第4'),
    (-1, '最終'),
]


class GoogleCalendarEventForm(forms.Form):

    community_name = forms.CharField(
        label='コミュニティ',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'readonly': True}),
    )

    start_date = forms.DateField(
        label='開始日',
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control'
        }),
        help_text='イベントの開始日を選択してください'
    )

    start_time = forms.TimeField(
        label='開始時刻',
        widget=forms.TimeInput(attrs={
            'type': 'time',
            'class': 'form-control'
        }),
        initial='22:00',
        help_text='イベントの開始時刻を選択してください'
    )

    duration = forms.IntegerField(
        label='開催時間（分）',
        initial=60,
        min_value=15,
        max_value=480,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        help_text='イベントの開催時間を分単位で入力してください'
    )

    recurrence_type = forms.ChoiceField(
        choices=RECURRENCE_CHOICES,
        label='開催周期',
        initial='none',
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
        help_text='イベントの開催周期を選択してください'
    )

    weekday = forms.ChoiceField(
        choices=CALENDAR_WEEKDAY_CHOICES,
        label='曜日',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text='週次・隔週・毎月同じ曜日の場合、開催する曜日を選択してください'
    )

    monthly_day = forms.IntegerField(
        label='開催日',
        required=False,
        min_value=1,
        max_value=31,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        help_text='毎月同じ日に開催する場合、その日付を選択してください（1-31）'
    )

    week_number = forms.ChoiceField(
        choices=WEEK_NUMBER_CHOICES,
        label='週',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text='毎月同じ曜日の場合、第何週かを選択してください'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # コミュニティが選択されている場合、そのコミュニティの設定を初期値として使用
        if 'initial' in kwargs and 'community' in kwargs['initial']:
            community = kwargs['initial']['community']
            if community:
                self.fields['start_time'].initial = community.start_time
                self.fields['duration'].initial = community.duration

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        recurrence_type = cleaned_data.get('recurrence_type')

        if start_date:
            # 過去の日付バリデーションを解除
            pass

            # 6ヶ月以上先の日付をチェック
            max_date = datetime.now().date() + timedelta(days=180)
            if start_date > max_date:
                raise ValidationError('6ヶ月以上先の日付は選択できません')

        # 定期開催の場合の追加バリデーション
        if recurrence_type in ['weekly', 'biweekly']:
            if not cleaned_data.get('weekday'):
                raise ValidationError('週次・隔週の場合は曜日を選択してください')

        elif recurrence_type == 'monthly_by_day':
            if not cleaned_data.get('weekday'):
                raise ValidationError('毎月同じ曜日の場合は曜日を選択してください')
            if not cleaned_data.get('week_number'):
                raise ValidationError('毎月同じ曜日の場合は第何週かを選択してください')

        elif recurrence_type == 'monthly_by_date':
            if not cleaned_data.get('monthly_day'):
                raise ValidationError('月次（日付指定）の場合は開催日を選択してください')

            # 31日がない月もあるため、28日までを推奨
            if cleaned_data.get('monthly_day') > 28:
                self.add_error(
                    'monthly_day', '月末の日付は月によって異なるため、28日以前の選択を推奨します')

        return cleaned_data
