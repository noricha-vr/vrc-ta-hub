# Create your models here.
from rest_framework import serializers

from community.models import Community
from event.models import Event, EventDetail, RecurrenceRule


class CommunitySerializer(serializers.ModelSerializer):
    poster_image = serializers.SerializerMethodField()

    class Meta:
        model = Community
        fields = [
            'id', 'name', 'created_at', 'updated_at', 'start_time', 'duration', 'weekdays',
            'frequency', 'organizers', 'group_url', 'organizer_url', 'sns_url',
            'discord', 'twitter_hashtag', 'poster_image', 'description',
            'platform', 'tags'
        ]

    def get_poster_image(self, obj):
        if obj.poster_image:
            return obj.poster_image.url
        return None


class EventSerializer(serializers.ModelSerializer):
    community = CommunitySerializer()  # ネストしてコミュニティ情報を含める

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
