# Create your models here.
from urllib.parse import urlparse

from rest_framework import serializers

from community.models import Community, WEEKDAY_CHOICES
from event.models import Event, EventDetail, RecurrenceRule
from ta_hub.libs import cloudflare_image_url


def _extract_group_id(group_url):
    """group_url からVRChatグループIDを抽出する。

    vrc.group: パス直下のコード（例: VRCTS.5197）
    vrchat.com: grp_ で始まるセグメント（例: grp_ad1356bc-...）
    """
    if not group_url:
        return None
    parsed = urlparse(group_url)
    host = parsed.netloc.lower()
    segments = [s for s in parsed.path.split('/') if s]
    if not segments:
        return None
    if 'vrc.group' in host:
        return segments[0]
    if 'vrchat.com' in host:
        return next((s for s in segments if s.startswith('grp_')), None)
    return None


class CommunitySerializer(serializers.ModelSerializer):
    poster_image = serializers.SerializerMethodField()
    group_id = serializers.SerializerMethodField()
    start_time = serializers.TimeField(format='%H:%M')

    class Meta:
        model = Community
        fields = [
            'id', 'name', 'created_at', 'updated_at', 'start_time', 'duration', 'weekdays',
            'frequency', 'organizers', 'group_url', 'group_id', 'organizer_url', 'sns_url',
            'discord', 'twitter_hashtag', 'poster_image', 'description',
            'platform', 'tags', 'allow_poster_repost'
        ]

    def get_poster_image(self, obj):
        if obj.poster_image:
            return cloudflare_image_url(obj.poster_image.url, width=800)
        return None

    def get_group_id(self, obj):
        return _extract_group_id(obj.group_url)


class GatheringListSerializer(serializers.Serializer):
    """TaAGatheringListSys 向けの JSON 形式に変換する。"""

    GENRE_LABELS = {
        'tech': '技術系',
        'academic': '学術系',
    }
    WEEKDAY_LABELS = dict(WEEKDAY_CHOICES)
    WEEKDAY_ORDER = {
        'Sun': 0,
        'Mon': 1,
        'Tue': 2,
        'Wed': 3,
        'Thu': 4,
        'Fri': 5,
        'Sat': 6,
        'Other': 7,
    }
    FIELD_DEFINITIONS = (
        ('ジャンル', serializers.CharField),
        ('曜日', serializers.CharField),
        ('イベント名', serializers.CharField),
        ('開始時刻', serializers.CharField),
        ('開催周期', serializers.CharField),
        ('主催・副主催', serializers.CharField),
        ('Join先', serializers.CharField),
        ('グループID', serializers.CharField),
        ('Discord', serializers.CharField),
        ('Twitter', serializers.CharField),
        ('ハッシュタグ', serializers.CharField),
        ('ポスター', serializers.URLField),
        ('イベント紹介', serializers.CharField),
        ('ポスター転載可', serializers.BooleanField),
    )

    def get_fields(self):
        fields = {}
        for field_name, field_class in self.FIELD_DEFINITIONS:
            field_kwargs = {'allow_null': True}
            if field_class is serializers.CharField:
                field_kwargs['allow_blank'] = True
            fields[field_name] = field_class(**field_kwargs)
        return fields

    @classmethod
    def normalize_choice_list(cls, value):
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, str):
            return [value] if value else []
        return []

    @classmethod
    def get_primary_weekday_code(cls, value):
        weekdays = cls.normalize_choice_list(value)
        return weekdays[0] if weekdays else 'Other'

    @classmethod
    def get_weekday_sort_index(cls, value):
        weekday = cls.get_primary_weekday_code(value)
        return cls.WEEKDAY_ORDER.get(weekday, cls.WEEKDAY_ORDER['Other'])

    def to_representation(self, instance):
        tags = self.normalize_choice_list(instance.tags)
        genre = (
            self.GENRE_LABELS['tech']
            if 'tech' in tags
            else self.GENRE_LABELS['academic']
            if 'academic' in tags
            else 'その他'
        )

        primary_weekday = self.get_primary_weekday_code(instance.weekdays)

        return {
            'ジャンル': genre,
            '曜日': self.WEEKDAY_LABELS.get(primary_weekday, 'その他'),
            'イベント名': instance.name,
            '開始時刻': instance.start_time.strftime('%H:%M') if instance.start_time else '',
            '開催周期': instance.frequency or '',
            '主催・副主催': instance.organizers or '',
            'Join先': instance.group_url or instance.organizer_url or '',
            'グループID': _extract_group_id(instance.group_url),
            'Discord': instance.discord or '',
            'Twitter': instance.sns_url or '',
            'ハッシュタグ': instance.twitter_hashtag or '',
            'ポスター': self._build_absolute_url(self._get_poster_url(instance)),
            'イベント紹介': instance.description or '',
            'ポスター転載可': instance.allow_poster_repost,
        }

    def _get_poster_url(self, instance):
        poster_image = getattr(instance, 'poster_image', None)
        if not poster_image:
            return None

        try:
            return poster_image.url
        except ValueError:
            return None

    def _build_absolute_url(self, url):
        if not url:
            return None
        if urlparse(url).scheme:
            return url

        request = self.context.get('request')
        if request is None:
            return url
        return request.build_absolute_uri(url)


class EventSerializer(serializers.ModelSerializer):
    community = CommunitySerializer()  # ネストしてコミュニティ情報を含める
    start_time = serializers.TimeField(format='%H:%M')

    class Meta:
        model = Event
        fields = ['id', 'community', 'date', 'start_time', 'duration', 'weekday']


class EventDetailSerializer(serializers.ModelSerializer):
    event = EventSerializer()  # ネストしてイベント情報を含める

    class Meta:
        model = EventDetail
        fields = [
            'id', 'event', 'start_time', 'duration', 'youtube_url', 'slide_url',
            'speaker', 'theme', 'additional_info'
        ]


class EventDetailWriteSerializer(serializers.ModelSerializer):
    """EventDetail作成・更新用シリアライザー"""
    generate_from_pdf = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = EventDetail
        fields = [
            'id', 'event', 'detail_type', 'start_time', 'duration', 'youtube_url',
            'slide_url', 'slide_file', 'speaker', 'theme', 'h1', 'contents',
            'meta_description', 'additional_info', 'generate_from_pdf'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        
    def validate_event(self, value):
        """イベントの所有者確認"""
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            # Superuserは全てのイベントにアクセス可能
            if request.user.is_superuser:
                return value
            # 一般ユーザーは自分のコミュニティのイベントのみ
            if not value.community.is_manager(request.user):
                raise serializers.ValidationError("このイベントへの権限がありません。")
        return value
    
    def create(self, validated_data):
        generate_from_pdf = validated_data.pop('generate_from_pdf', False)
        instance = super().create(validated_data)
        
        # PDF自動生成が有効で、PDFファイルがある場合
        if generate_from_pdf and instance.slide_file:
            # ここでPDF処理タスクをトリガー（後で実装）
            pass
            
        return instance
    
    def update(self, instance, validated_data):
        generate_from_pdf = validated_data.pop('generate_from_pdf', False)
        instance = super().update(instance, validated_data)
        
        # PDF自動生成が有効で、PDFファイルがある場合
        if generate_from_pdf and instance.slide_file:
            # ここでPDF処理タスクをトリガー（後で実装）
            pass
            
        return instance


class RecurrenceRuleSerializer(serializers.ModelSerializer):
    """RecurrenceRuleのシリアライザー"""
    future_events_count = serializers.SerializerMethodField()
    
    class Meta:
        model = RecurrenceRule
        fields = [
            'id', 'frequency', 'interval', 'week_of_month', 'custom_rule',
            'start_date', 'end_date', 'created_at', 'updated_at', 'future_events_count'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'future_events_count']
    
    def get_future_events_count(self, obj):
        """未来のイベント数を取得"""
        from django.utils import timezone
        today = timezone.now().date()
        count = Event.objects.filter(
            recurrence_rule=obj,
            date__gte=today
        ).count()
        
        # インスタンスイベントも含める
        master_events = Event.objects.filter(
            recurrence_rule=obj,
            is_recurring_master=True
        )
        for master in master_events:
            count += master.recurring_instances.filter(date__gte=today).count()
        
        return count


class RecurrenceRuleDeleteSerializer(serializers.Serializer):
    """RecurrenceRule削除用のシリアライザー"""
    delete_from_date = serializers.DateField(
        required=False,
        help_text="この日付以降のイベントを削除（指定しない場合は今日以降）"
    )
    delete_rule = serializers.BooleanField(
        default=True,
        help_text="定期ルール自体も削除するか"
    )
