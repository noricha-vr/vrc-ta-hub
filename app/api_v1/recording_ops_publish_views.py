"""録画運用クライアント向け YouTube 公開完了同期 API ビュー。"""
from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from event.services.recording_ops.errors import RecordingOpsError
from event.services.recording_ops.publish_sync import mark_recording_item_published

from .authentication import APIKeyAuthentication
from .recording_ops_serializers import RecordingOpsErrorSerializer
from .recording_ops_views import _error_response
from .views import IsSuperuser


class RecordingOpsMarkPublishedRequestSerializer(serializers.Serializer):
    video_id = serializers.CharField(max_length=32, allow_blank=False)


class RecordingOpsMarkPublishedResponseSerializer(serializers.Serializer):
    event_detail_id = serializers.IntegerField()
    synced = serializers.BooleanField()
    already_published = serializers.BooleanField()
    compensating_stop_enqueued = serializers.BooleanField()
    reason = serializers.CharField()
    review_status = serializers.CharField(allow_blank=True)
    is_public_locked = serializers.BooleanField()


class RecordingOpsMarkPublishedAPIView(APIView):
    """スーパーユーザー API キー（write スコープ）限定で公開完了の事実を RecordingReviewItem へ同期する。"""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated, IsSuperuser]
    parser_classes = [JSONParser]

    @extend_schema(
        summary="録画運用向け YouTube 公開完了の同期",
        description=(
            "スーパーユーザーの API キー（write スコープ）限定。"
            "YouTube で公開完了した動画 ID を RecordingReviewItem へ冪等に記録し、公開ロックを設定します。"
            "同意無効・削除済みとの競合時は補償公開停止を自動起票して 409 を返します。"
        ),
        tags=["Recording Ops"],
        request=RecordingOpsMarkPublishedRequestSerializer,
        responses={
            200: RecordingOpsMarkPublishedResponseSerializer,
            400: RecordingOpsErrorSerializer,
            403: RecordingOpsErrorSerializer,
            404: RecordingOpsErrorSerializer,
            409: RecordingOpsErrorSerializer,
        },
    )
    def post(self, request, detail_id: int) -> Response:
        if not isinstance(request.data, dict):
            raise ValidationError({"detail": "JSON オブジェクトを指定してください。"})
        serializer = RecordingOpsMarkPublishedRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = mark_recording_item_published(
                detail_id,
                video_id=serializer.validated_data["video_id"],
            )
        except RecordingOpsError as exc:
            return _error_response(exc)
        return Response(
            RecordingOpsMarkPublishedResponseSerializer(result).data,
            status=status.HTTP_200_OK,
        )
