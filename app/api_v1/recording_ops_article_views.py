"""録画運用クライアント向け 記事生成ステータス一括取得 API ビュー。"""
from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from event.models import EventDetail
from event.services.recording_ops.article_status import resolve_machine_article_status

from .authentication import APIKeyAuthentication
from .recording_ops_serializers import RecordingOpsErrorSerializer
from .views import IsSuperuser

MAX_ARTICLE_STATUS_IDS = 100


class RecordingOpsArticleStatusItemSerializer(serializers.Serializer):
    detail_id = serializers.IntegerField()
    consent = serializers.CharField()
    state = serializers.ChoiceField(
        choices=[
            "generated_current_video",
            "generated_pdf_only",
            "manual",
            "queued",
            "failed",
            "not_queued",
            "not_applicable",
        ]
    )
    has_video = serializers.BooleanField()
    video_id = serializers.CharField(allow_blank=True)
    article_source_video_id = serializers.CharField(allow_blank=True)
    attempts = serializers.IntegerField()
    deferrals = serializers.IntegerField()
    next_attempt_at = serializers.DateTimeField(allow_null=True)
    article_generated_at = serializers.DateTimeField(allow_null=True)
    last_error_code = serializers.CharField(allow_blank=True)


class RecordingOpsArticleStatusResponseSerializer(serializers.Serializer):
    items = serializers.DictField(child=RecordingOpsArticleStatusItemSerializer())


def _parse_detail_ids(raw: str | None) -> list[int]:
    if not raw or not raw.strip():
        raise ValidationError({"ids": "1 件以上の detail_id をカンマ区切りで指定してください。"})
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts:
        raise ValidationError({"ids": "1 件以上の detail_id をカンマ区切りで指定してください。"})
    ids: list[int] = []
    seen: set[int] = set()
    for part in parts:
        if not part.isdigit():
            raise ValidationError({"ids": f"不正な detail_id です: {part}"})
        val = int(part)
        if val <= 0:
            raise ValidationError({"ids": f"detail_id は正の整数を指定してください: {part}"})
        if val not in seen:
            seen.add(val)
            ids.append(val)
    if len(ids) > MAX_ARTICLE_STATUS_IDS:
        raise ValidationError({"ids": f"一度に指定できる detail_id は最大 {MAX_ARTICLE_STATUS_IDS} 件です。"})
    return ids


class RecordingOpsArticleStatusAPIView(APIView):
    """スーパーユーザー API キー限定で複数発表の記事生成ステータスを一括返却する。"""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated, IsSuperuser]

    @extend_schema(
        summary="録画運用向け 記事生成ステータスの一括取得",
        description=(
            "スーパーユーザーの API キー限定。"
            "指定した EventDetail ID 群の記事生成状態（generated_current_video / generated_pdf_only / "
            "manual / queued / failed / not_queued / not_applicable）を一括返却します。"
        ),
        tags=["Recording Ops"],
        parameters=[
            OpenApiParameter(
                name="ids",
                type=str,
                location=OpenApiParameter.QUERY,
                required=True,
                description=f"カンマ区切りの EventDetail ID（最大 {MAX_ARTICLE_STATUS_IDS} 件）",
            ),
        ],
        responses={
            200: RecordingOpsArticleStatusResponseSerializer,
            400: RecordingOpsErrorSerializer,
            403: RecordingOpsErrorSerializer,
        },
    )
    def get(self, request) -> Response:
        detail_ids = _parse_detail_ids(request.query_params.get("ids"))
        details = EventDetail.objects.filter(
            pk__in=detail_ids,
            deleted_at__isnull=True,
        ).only(
            "id",
            "status",
            "youtube_url",
            "h1",
            "contents",
            "meta_description",
            "article_consent",
            "article_generation_requested_at",
            "article_generation_attempts",
            "article_generation_deferrals",
            "article_generation_last_error",
            "article_generated_at",
            "article_source_video_id",
            "article_body_hash",
        )
        items = {str(detail.pk): resolve_machine_article_status(detail) for detail in details}
        return Response({"items": items}, status=status.HTTP_200_OK)
