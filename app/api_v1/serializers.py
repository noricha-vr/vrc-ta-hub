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
