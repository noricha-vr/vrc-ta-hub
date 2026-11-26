"""動画編集プロキシ R2 署名配信・EDL v1 確定指示キュー API シリアライザ。"""
from __future__ import annotations

import math

from rest_framework import serializers

from event.models import RecordingEditOrder, RecordingReviewItem
from event.services.recording_ops.errors import MAX_REVISION


class StrictIntegerField(serializers.IntegerField):
    """JSON で bool / str / float からの暗黙変換を許さない厳格な整数フィールド。"""

    def to_internal_value(self, data):
        if type(data) is not int:
            self.fail('invalid')
        return super().to_internal_value(data)


class StrictFloatField(serializers.FloatField):
    """JSON で bool / str / NaN / Inf / オーバーフローを拒否する厳格な浮動小数点数フィールド。"""

    def to_internal_value(self, data):
        if isinstance(data, bool) or not isinstance(data, (int, float)):
            self.fail('invalid')
        try:
            value = super().to_internal_value(data)
        except OverflowError:
            self.fail('invalid')
        if not math.isfinite(value):
            self.fail('invalid')
        return value


class StrictBooleanField(serializers.BooleanField):
    """JSON で真偽値リテラルのみ受け付ける厳格な真偽値フィールド。"""

    def to_internal_value(self, data):
        if not isinstance(data, bool):
            self.fail('invalid')
        return super().to_internal_value(data)


class StrictJSONDictField(serializers.JSONField):
    """JSON オブジェクト（dict）のみ受け付ける JSON フィールド。"""

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not isinstance(value, dict):
            self.fail('invalid')
        return value


class ProxyUploadUrlRequestSerializer(serializers.Serializer):
    expires_in = StrictIntegerField(required=False)
    include_transcript = StrictBooleanField(required=False, default=False)
    transcript_ext = serializers.ChoiceField(
        choices=['json', 'txt', 'srt', 'vtt'],
        required=False,
        default='json',
    )


class ProxyUploadUrlResponseSerializer(serializers.Serializer):
    event_detail_id = serializers.IntegerField()
    object_key = serializers.CharField()
    upload_url = serializers.URLField()
    method = serializers.CharField()
    headers = serializers.DictField(child=serializers.CharField())
    expires_in = serializers.IntegerField()
    expires_at = serializers.DateTimeField()
    transcript_object_key = serializers.CharField(allow_null=True)
    transcript_upload_url = serializers.URLField(allow_null=True)
    transcript_headers = serializers.DictField(child=serializers.CharField(), allow_null=True)


class RecordingReviewItemUpsertRequestSerializer(serializers.Serializer):
    proxy_r2_key = serializers.CharField(required=False, max_length=255, trim_whitespace=False)
    transcript_r2_key = serializers.CharField(
        required=False, allow_blank=True, max_length=255, trim_whitespace=False,
    )
    proxy_duration_sec = StrictFloatField(required=False)
    proxy_uploaded_at = serializers.DateTimeField(required=False)
    proxy_expires_at = serializers.DateTimeField(required=False)
    review_status = serializers.ChoiceField(
        choices=[
            RecordingReviewItem.ReviewStatus.PROCESSING,
            RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
        ],
        required=False,
    )
    edl_json = StrictJSONDictField(required=False)
    youtube_draft_video_id = serializers.CharField(
        required=False, allow_blank=True, max_length=32, trim_whitespace=False,
    )
    youtube_draft_privacy = serializers.ChoiceField(
        choices=[
            '',
            RecordingReviewItem.YouTubePrivacy.UNLISTED,
            RecordingReviewItem.YouTubePrivacy.PRIVATE,
        ],
        required=False,
    )
    expected_revision = StrictIntegerField(required=False, min_value=1, max_value=MAX_REVISION)


class RecordingReviewItemSerializer(serializers.ModelSerializer):
    event_detail_id = serializers.IntegerField(read_only=True, allow_null=True)
    is_public_locked = serializers.BooleanField(read_only=True)

    class Meta:
        model = RecordingReviewItem
        fields = [
            'id',
            'event_detail_id',
            'proxy_r2_key',
            'transcript_r2_key',
            'proxy_duration_sec',
            'proxy_uploaded_at',
            'proxy_expires_at',
            'review_status',
            'edl_revision',
            'worker_synced_revision',
            'edl_json',
            'youtube_draft_video_id',
            'youtube_draft_privacy',
            'is_public_locked',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class ProxyDownloadUrlResponseSerializer(serializers.Serializer):
    event_detail_id = serializers.IntegerField()
    proxy_url = serializers.URLField()
    expires_in = serializers.IntegerField()
    expires_at = serializers.DateTimeField()
    proxy_duration_sec = serializers.FloatField(allow_null=True)
    proxy_expires_at = serializers.DateTimeField(allow_null=True)
    review_status = serializers.ChoiceField(choices=RecordingReviewItem.ReviewStatus.choices)
    edl_revision = serializers.IntegerField()
    worker_synced_revision = serializers.IntegerField(allow_null=True)
    edl_json = serializers.JSONField()
    youtube_draft_video_id = serializers.CharField()
    youtube_draft_privacy = serializers.CharField()
    is_public_locked = serializers.BooleanField()
    has_latest_order_failed = serializers.BooleanField()
    has_pending_stop_videos = serializers.BooleanField()
    compensating_stop_pending = serializers.BooleanField()


class RecordingEditOrderCreateRequestSerializer(serializers.Serializer):
    expected_revision = StrictIntegerField(required=True, min_value=1, max_value=MAX_REVISION)
    order_type = serializers.ChoiceField(
        choices=RecordingEditOrder.OrderType.choices,
        required=False,
        default=RecordingEditOrder.OrderType.APPLY_EDL,
    )
    edl_json = StrictJSONDictField(required=False)


class RecordingReviewItemStopPublishRequestSerializer(serializers.Serializer):
    expected_revision = StrictIntegerField(required=True, min_value=1, max_value=MAX_REVISION)


class RecordingEditOrderSerializer(serializers.ModelSerializer):
    review_item_id = serializers.IntegerField(read_only=True)
    event_detail_id = serializers.IntegerField(read_only=True, allow_null=True)
    requested_by_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = RecordingEditOrder
        fields = [
            'id',
            'review_item_id',
            'event_detail_id',
            'order_type',
            'target_video_id',
            'base_revision',
            'target_revision',
            'edl_snapshot',
            'status',
            'requested_by_id',
            'result_video_id',
            'error_message',
            'created_at',
            'started_at',
            'completed_at',
            'updated_at',
        ]
        read_only_fields = fields


class ClaimedRecordingEditOrderSerializer(RecordingEditOrderSerializer):
    proxy_r2_key = serializers.CharField(source='review_item.proxy_r2_key', read_only=True)
    transcript_r2_key = serializers.CharField(source='review_item.transcript_r2_key', read_only=True)
    youtube_draft_video_id = serializers.CharField(source='review_item.youtube_draft_video_id', read_only=True)
    youtube_draft_privacy = serializers.CharField(source='review_item.youtube_draft_privacy', read_only=True)

    class Meta(RecordingEditOrderSerializer.Meta):
        fields = [
            *RecordingEditOrderSerializer.Meta.fields,
            'proxy_r2_key',
            'transcript_r2_key',
            'youtube_draft_video_id',
            'youtube_draft_privacy',
        ]
        read_only_fields = fields


class RecordingEditOrderClaimRequestSerializer(serializers.Serializer):
    order_types = serializers.ListField(
        child=serializers.ChoiceField(choices=RecordingEditOrder.OrderType.choices),
        required=False,
        allow_empty=False,
    )


class RecordingEditOrderClaimResponseSerializer(serializers.Serializer):
    order = ClaimedRecordingEditOrderSerializer(allow_null=True)


class RecordingEditOrderCompleteRequestSerializer(serializers.Serializer):
    target_revision = StrictIntegerField(required=True, min_value=1, max_value=MAX_REVISION)
    status = serializers.ChoiceField(
        choices=[
            RecordingEditOrder.Status.SUCCEEDED,
            RecordingEditOrder.Status.FAILED,
        ],
        required=True,
    )
    result_video_id = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=32, trim_whitespace=False,
    )
    youtube_url = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=200, trim_whitespace=False,
    )
    youtube_draft_privacy = serializers.ChoiceField(
        choices=[
            '',
            RecordingReviewItem.YouTubePrivacy.UNLISTED,
            RecordingReviewItem.YouTubePrivacy.PRIVATE,
            RecordingReviewItem.YouTubePrivacy.PUBLIC,
        ],
        required=False,
        default='',
    )
    review_status = serializers.ChoiceField(
        choices=[
            '',
            RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
            RecordingReviewItem.ReviewStatus.PUBLISHED,
            RecordingReviewItem.ReviewStatus.PRIVATE_FIXED,
        ],
        required=False,
        default='',
    )
    error_message = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=10000, trim_whitespace=False,
    )


class RecordingOpsErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()
    code = serializers.CharField()
    current = serializers.DictField(required=False)
