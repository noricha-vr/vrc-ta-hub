"""録画運用ダッシュボード向け統計スナップショット受信 API ビュー。"""
from __future__ import annotations

import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import APIKeyAuthentication
from .recording_ops_serializers import RecordingOpsErrorSerializer
from .recording_ops_snapshot_serializers import (
    MAX_SNAPSHOT_PAYLOAD_BYTES,
    RecordingOpsSnapshotIngestSerializer,
    RecordingOpsSnapshotResponseSerializer,
)
from .views import IsSuperuser

logger = logging.getLogger(__name__)


class RecordingOpsSnapshotCreateAPIView(APIView):
    """外部録画運用クライアントからの統計スナップショットを受信して保存する。"""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated, IsSuperuser]
    parser_classes = [JSONParser]

    @extend_schema(
        summary="録画運用スナップショットの登録",
        description=(
            "スーパーユーザーの書き込み API キー限定。"
            "schema_version=1 の録画運用統計スナップショットを検証して保存します。"
        ),
        tags=["Recording Ops"],
        request=RecordingOpsSnapshotIngestSerializer,
        responses={
            201: RecordingOpsSnapshotResponseSerializer,
            400: RecordingOpsErrorSerializer,
            403: RecordingOpsErrorSerializer,
        },
    )
    def post(self, request) -> Response:
        body_bytes = len(request.body)
        if body_bytes > MAX_SNAPSHOT_PAYLOAD_BYTES:
            logger.warning(
                "recording_ops_snapshot_rejected",
                extra={
                    "event_type": "recording_ops_snapshot_rejected",
                    "reason": "payload_too_large",
                    "payload_bytes": body_bytes,
                },
            )
            raise ValidationError(
                {"detail": f"スナップショットのサイズが上限（{MAX_SNAPSHOT_PAYLOAD_BYTES} バイト）を超えています。"}
            )

        if not isinstance(request.data, dict):
            logger.warning(
                "recording_ops_snapshot_rejected",
                extra={
                    "event_type": "recording_ops_snapshot_rejected",
                    "reason": "invalid_root_type",
                    "payload_bytes": body_bytes,
                },
            )
            raise ValidationError({"detail": "JSON オブジェクトを指定してください。"})

        serializer = RecordingOpsSnapshotIngestSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning(
                "recording_ops_snapshot_rejected",
                extra={
                    "event_type": "recording_ops_snapshot_rejected",
                    "reason": "validation_error",
                    "payload_bytes": body_bytes,
                },
            )
            raise ValidationError(serializer.errors)

        snapshot = serializer.save()
        logger.info(
            "recording_ops_snapshot_received",
            extra={
                "event_type": "recording_ops_snapshot_received",
                "snapshot_id": snapshot.pk,
                "schema_version": snapshot.schema_version,
                "snapshot_at": snapshot.snapshot_at.isoformat(),
                "payload_bytes": serializer.validated_data["_payload_bytes"],
            },
        )
        return Response(
            RecordingOpsSnapshotResponseSerializer(snapshot).data,
            status=status.HTTP_201_CREATED,
        )
