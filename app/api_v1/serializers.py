# Create your models here.
from rest_framework import serializers

from community.models import Community
from event.models import Event, EventDetail


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
            'speaker', 'theme'
        ]


class EventDetailWriteSerializer(serializers.ModelSerializer):
    """EventDetail作成・更新用シリアライザー"""
    generate_from_pdf = serializers.BooleanField(write_only=True, required=False, default=False)
    
    class Meta:
        model = EventDetail
        fields = [
            'id', 'event', 'detail_type', 'start_time', 'duration', 'youtube_url', 
            'slide_url', 'slide_file', 'speaker', 'theme', 'h1', 'contents', 
            'meta_description', 'generate_from_pdf'
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
            if value.community.custom_user != request.user:
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
