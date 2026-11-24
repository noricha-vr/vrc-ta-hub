"""動画編集プロキシ R2 署名配信・EDL v1 確定指示キュー API ビュー。"""
from __future__ import annotations

import re

from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from event.models import EventDetail
from event.services.recording_items import upsert_recording_item
from event.services.recording_ops_common import (
    RecordingConflictError,
    RecordingNotFoundError,
    RecordingOpsError,
    RecordingValidationError,
)
from event.services.recording_order_worker import (
    claim_next_edit_order,
    complete_edit_order,
)
from event.services.recording_orders import create_edit_order
from event.services.recording_storage import (
    RecordingConsentError,
    RecordingObjectKeyError,
    RecordingProxyExpiredError,
    RecordingProxyNotFoundError,
    RecordingStorageConfigError,
    RecordingStorageError,
    create_presigned_proxy_download_url,
    create_presigned_proxy_upload_url,
)

from .authentication import APIKeyAuthentication
from .recording_ops_serializers import (
    ClaimedRecordingEditOrderSerializer,
    ProxyDownloadUrlResponseSerializer,
    ProxyUploadUrlRequestSerializer,
    ProxyUploadUrlResponseSerializer,
    RecordingEditOrderClaimRequestSerializer,
    RecordingEditOrderClaimResponseSerializer,
    RecordingEditOrderCompleteRequestSerializer,
    RecordingEditOrderCreateRequestSerializer,
    RecordingEditOrderSerializer,
    RecordingOpsErrorSerializer,
    RecordingReviewItemSerializer,
    RecordingReviewItemUpsertRequestSerializer,
)
from .views import IsSuperuser


class IsStaffOrSuperuser(BasePermission):
    message = '運営スタッフまたは管理者のみ利用できます。'

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and (user.is_staff or user.is_superuser)
        )


def _require_json_dict(request) -> dict:
    if not isinstance(request.data, dict):
        raise ValidationError({'detail': 'JSON オブジェクトを指定してください。'})
    return request.data


def _parse_optional_query_int(raw_value: str | None, *, name: str) -> int | None:
    if raw_value is None or raw_value == '':
        return None
    if len(raw_value) > 20 or re.fullmatch(r'-?[0-9]+', raw_value) is None:
        raise ValidationError({name: f'{name} は整数で指定してください。'})
    return int(raw_value)


def _error_response(exc: RecordingOpsError | RecordingStorageError) -> Response:
    if isinstance(exc, (RecordingNotFoundError, RecordingProxyNotFoundError)):
        http_status = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, RecordingConsentError):
        http_status = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, RecordingProxyExpiredError):
        http_status = status.HTTP_410_GONE
    elif isinstance(exc, RecordingConflictError):
        body: dict[str, object] = {'detail': exc.detail, 'code': exc.code}
        if exc.current_state is not None:
            body['current'] = exc.current_state
        return Response(body, status=status.HTTP_409_CONFLICT)
    elif isinstance(exc, (RecordingValidationError, RecordingObjectKeyError)):
        http_status = status.HTTP_400_BAD_REQUEST
    elif isinstance(exc, (RecordingStorageConfigError, RecordingStorageError)):
        http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    else:
        http_status = status.HTTP_400_BAD_REQUEST
    return Response({'detail': exc.detail, 'code': exc.code}, status=http_status)


class RecordingProxyUploadUrlAPIView(APIView):
    """非公開 R2 バケットへの編集プロキシ動画 PUT 署名 URL を発給する。"""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated, IsSuperuser]
    parser_classes = [JSONParser]

    @extend_schema(
        summary='編集プロキシ動画の署名 PUT URL 発給',
        description=(
            'スーパーユーザーの書き込み API キー限定。'
            'recording_policy=public かつ集会の recording_allowed=true の発表に限り、'
            '非公開 R2 バケット（recording-proxy/<detail_id>/<uuid>.mp4）への署名 PUT URL を発給します。'
        ),
        tags=['Recording Ops'],
        request=ProxyUploadUrlRequestSerializer,
        responses={
            200: ProxyUploadUrlResponseSerializer,
            400: RecordingOpsErrorSerializer,
            403: RecordingOpsErrorSerializer,
            404: RecordingOpsErrorSerializer,
        },
    )
    def post(self, request, detail_id: int) -> Response:
        data = _require_json_dict(request)
        serializer = ProxyUploadUrlRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)

        detail = get_object_or_404(EventDetail.objects.select_related('event__community'), pk=detail_id)
        try:
            result = create_presigned_proxy_upload_url(
                detail,
                expires_in=serializer.validated_data.get('expires_in'),
                include_transcript=serializer.validated_data['include_transcript'],
                transcript_ext=serializer.validated_data['transcript_ext'],
            )
        except (RecordingOpsError, RecordingStorageError) as exc:
            return _error_response(exc)

        payload = ProxyUploadUrlResponseSerializer(result).data
        return Response(payload, status=status.HTTP_200_OK)


class RecordingReviewItemUpsertAPIView(APIView):
    """プロキシ動画メタ情報および初期 EDL v1 を登録・更新する。"""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated, IsSuperuser]
    parser_classes = [JSONParser]

    @extend_schema(
        summary='録画プロキシ・EDL v1 状態の登録・更新',
        description=(
            'スーパーユーザーの書き込み API キー限定。'
            '初回登録は proxy_r2_key と edl_json が必須（edl_revision=1）。'
            '既存項目の更新は expected_revision が必須で、公開済みロック中や進行中オーダーがある場合は 409 Conflict を返します。'
        ),
        tags=['Recording Ops'],
        request=RecordingReviewItemUpsertRequestSerializer,
        responses={
            200: RecordingReviewItemSerializer,
            201: RecordingReviewItemSerializer,
            400: RecordingOpsErrorSerializer,
            403: RecordingOpsErrorSerializer,
            404: RecordingOpsErrorSerializer,
            409: RecordingOpsErrorSerializer,
        },
    )
    def put(self, request, detail_id: int) -> Response:
        data = _require_json_dict(request)
        serializer = RecordingReviewItemUpsertRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        try:
            item, created = upsert_recording_item(
                detail_id,
                proxy_r2_key=validated.get('proxy_r2_key'),
                transcript_r2_key=validated.get('transcript_r2_key'),
                proxy_duration_sec=validated.get('proxy_duration_sec'),
                proxy_uploaded_at=validated.get('proxy_uploaded_at'),
                proxy_expires_at=validated.get('proxy_expires_at'),
                review_status=validated.get('review_status'),
                edl_json=validated.get('edl_json'),
                youtube_draft_video_id=validated.get('youtube_draft_video_id'),
                youtube_draft_privacy=validated.get('youtube_draft_privacy'),
                expected_revision=validated.get('expected_revision'),
            )
        except (RecordingOpsError, RecordingStorageError) as exc:
            return _error_response(exc)

        http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(RecordingReviewItemSerializer(item).data, status=http_status)


class RecordingProxyDownloadUrlAPIView(APIView):
    """スタッフ確認画面向けに非公開 R2 プロキシ動画の署名 GET URL を発給する。"""

    authentication_classes = [APIKeyAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated, IsStaffOrSuperuser]

    @extend_schema(
        summary='編集プロキシ動画の署名 GET URL 発給',
        description=(
            '運営スタッフ（is_staff）または管理者（is_superuser）限定。'
            'recording_policy=public かつ集会の recording_allowed=true の発表に限り、'
            '15〜30 分有効な非公開 R2 署名 GET URL と現在の EDL v1 状態を返します。'
        ),
        tags=['Recording Ops'],
        parameters=[
            OpenApiParameter(
                name='expires_in',
                description='署名 URL の有効秒数（900〜1800 にクランプ、既定 1200）',
                required=False,
                location=OpenApiParameter.QUERY,
                type=OpenApiTypes.INT,
            ),
        ],
        responses={
            200: ProxyDownloadUrlResponseSerializer,
            400: RecordingOpsErrorSerializer,
            403: RecordingOpsErrorSerializer,
            404: RecordingOpsErrorSerializer,
            410: RecordingOpsErrorSerializer,
        },
    )
    def get(self, request, detail_id: int) -> Response:
        expires_in = _parse_optional_query_int(request.query_params.get('expires_in'), name='expires_in')
        detail = get_object_or_404(EventDetail.objects.select_related('event__community'), pk=detail_id)
        try:
            result = create_presigned_proxy_download_url(detail, expires_in=expires_in)
        except (RecordingOpsError, RecordingStorageError) as exc:
            return _error_response(exc)

        item = result.review_item
        payload = ProxyDownloadUrlResponseSerializer({
            'event_detail_id': detail.pk,
            'proxy_url': result.proxy_url,
            'expires_in': result.expires_in,
            'expires_at': result.expires_at,
            'proxy_duration_sec': item.proxy_duration_sec,
            'proxy_expires_at': item.proxy_expires_at,
            'review_status': item.review_status,
            'edl_revision': item.edl_revision,
            'edl_json': item.edl_json,
            'youtube_draft_video_id': item.youtube_draft_video_id,
            'youtube_draft_privacy': item.youtube_draft_privacy,
            'is_public_locked': item.is_public_locked,
        }).data
        return Response(payload, status=status.HTTP_200_OK)


class RecordingEditOrderCreateAPIView(APIView):
    """EDL v1 確定指示（または公開停止指示）をキューへ登録する。"""

    authentication_classes = [APIKeyAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated, IsStaffOrSuperuser]
    parser_classes = [JSONParser]

    @extend_schema(
        summary='録画編集の確定指示キュー登録',
        description=(
            '運営スタッフ（is_staff）または管理者（is_superuser）限定（API キー利用時は write スコープ必須）。'
            'expected_revision を select_for_update 下で照合し、'
            'リビジョン不一致・進行中オーダー重複・公開済みロック時（apply_edl）は 409 Conflict を返します。'
        ),
        tags=['Recording Ops'],
        request=RecordingEditOrderCreateRequestSerializer,
        responses={
            201: RecordingEditOrderSerializer,
            400: RecordingOpsErrorSerializer,
            403: RecordingOpsErrorSerializer,
            404: RecordingOpsErrorSerializer,
            409: RecordingOpsErrorSerializer,
        },
    )
    def post(self, request, detail_id: int) -> Response:
        data = _require_json_dict(request)
        serializer = RecordingEditOrderCreateRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        try:
            order = create_edit_order(
                detail_id,
                expected_revision=validated['expected_revision'],
                order_type=validated['order_type'],
                edl_json=validated.get('edl_json'),
                requested_by=request.user,
            )
        except (RecordingOpsError, RecordingStorageError) as exc:
            return _error_response(exc)

        return Response(RecordingEditOrderSerializer(order).data, status=status.HTTP_201_CREATED)


class RecordingEditOrderClaimAPIView(APIView):
    """待機中（queued）の確定指示を 1 件リースして running へ遷移させる。"""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated, IsSuperuser]
    parser_classes = [JSONParser]

    @extend_schema(
        summary='確定指示キューのリース取得（claim）',
        description=(
            'スーパーユーザーの書き込み API キー限定。'
            '最古の queued オーダーを 1 件 running に遷移させて返します。'
            '待機中オーダーがない場合は {"order": null} を 200 OK で返します。'
        ),
        tags=['Recording Ops'],
        request=RecordingEditOrderClaimRequestSerializer,
        responses={
            200: RecordingEditOrderClaimResponseSerializer,
            400: RecordingOpsErrorSerializer,
            403: RecordingOpsErrorSerializer,
        },
    )
    def post(self, request) -> Response:
        data = _require_json_dict(request)
        serializer = RecordingEditOrderClaimRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)

        try:
            order = claim_next_edit_order(order_types=serializer.validated_data.get('order_types'))
        except (RecordingOpsError, RecordingStorageError) as exc:
            return _error_response(exc)

        payload = {'order': ClaimedRecordingEditOrderSerializer(order).data if order is not None else None}
        return Response(payload, status=status.HTTP_200_OK)


class RecordingEditOrderCompleteAPIView(APIView):
    """処理中（running）の確定指示の完了/失敗を報告する。"""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated, IsSuperuser]
    parser_classes = [JSONParser]

    @extend_schema(
        summary='確定指示の完了・失敗報告',
        description=(
            'スーパーユーザーの書き込み API キー限定。'
            'order_id と target_revision、および running 状態を検証し、'
            '不一致・既終端時は 409 Conflict を返します。'
            '成功時は active_lock_key を解放し、検証済みの youtube_url があれば発表へ反映します。'
        ),
        tags=['Recording Ops'],
        request=RecordingEditOrderCompleteRequestSerializer,
        responses={
            200: RecordingEditOrderSerializer,
            400: RecordingOpsErrorSerializer,
            403: RecordingOpsErrorSerializer,
            404: RecordingOpsErrorSerializer,
            409: RecordingOpsErrorSerializer,
        },
    )
    def post(self, request, order_id: int) -> Response:
        data = _require_json_dict(request)
        serializer = RecordingEditOrderCompleteRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        try:
            order = complete_edit_order(
                order_id,
                target_revision=validated['target_revision'],
                status=validated['status'],
                result_video_id=validated['result_video_id'],
                youtube_url=validated['youtube_url'],
                youtube_draft_privacy=validated['youtube_draft_privacy'],
                review_status=validated['review_status'],
                error_message=validated['error_message'],
            )
        except (RecordingOpsError, RecordingStorageError) as exc:
            return _error_response(exc)

        return Response(RecordingEditOrderSerializer(order).data, status=status.HTTP_200_OK)
