"""定期イベントプレビューAPI

request body validation は Pydantic (api_v1.input_schemas.RecurrencePreviewInput) に統一。
既存応答形式 {"success": bool, "error": str, "dates": [...], "count": int} は維持する。
"""
from pydantic import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from event.recurrence_service import RecurrenceService

from .input_schemas import (
    ERROR_DATE_FORMAT_PREFIX,
    RecurrencePreviewInput,
)


def _extract_error_message(exc: ValidationError) -> str:
    """Pydantic ValidationError から既存 API 互換のエラーメッセージを取り出す.

    Pydantic v2 の errors() は dict のリスト。優先順位:
        1. ctx.error が ValueError ならその str() を採用（field/model validator が raise した文言）
        2. それ以外は msg 先頭をそのまま採用
    既存テストは「基準日」「カスタムルール」「日付形式」の部分文字列マッチで判定するため、
    1 件目のエラーメッセージを返せば十分。
    """
    errors = exc.errors()
    if not errors:
        return "入力が不正です"
    first = errors[0]
    ctx = first.get("ctx") or {}
    inner = ctx.get("error")
    if inner is not None:
        return str(inner)
    # Pydantic の組み込みエラー（型違い等）はそのまま msg を返す。
    return str(first.get("msg") or "入力が不正です")


class RecurrencePreviewAPIView(APIView):
    """定期イベントの日付プレビューAPI"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """定期ルールから日付リストをプレビュー生成"""
        try:
            params = RecurrencePreviewInput(**request.data)
        except ValidationError as exc:
            message = _extract_error_message(exc)
            # 日付/時刻パースエラーは旧実装互換で dates/count も返す。
            if ERROR_DATE_FORMAT_PREFIX in message:
                return Response({
                    'success': False,
                    'error': message,
                    'dates': [],
                    'count': 0,
                }, status=status.HTTP_400_BAD_REQUEST)
            return Response({
                'success': False,
                'error': message,
            }, status=status.HTTP_400_BAD_REQUEST)

        # コミュニティを取得（オプション）
        community = None
        if params.community_id:
            from community.models import Community
            try:
                community = Community.objects.get(id=params.community_id)
            except Community.DoesNotExist:
                # 存在しないIDは任意指定の失効として扱い、
                # communityなしのプレビューを継続する（既存挙動を維持）。
                community = None

        try:
            # RecurrenceServiceを使用してプレビュー生成
            service = RecurrenceService()
            result = service.preview_dates(
                frequency=params.frequency,
                custom_rule=params.custom_rule,
                base_date=params.base_date,
                base_time=params.base_time,
                interval=params.interval,
                week_of_month=params.week_of_month,
                weekday=params.weekday,
                months=params.months,
                community=community,
            )

            if result['success']:
                return Response(result)

            # エラーメッセージをより詳細に
            error_msg = result.get('error', '日付の生成に失敗しました')
            if '複雑' in error_msg or '解釈' in error_msg:
                error_msg += '\n開催周期が複雑な場合は、複数のシンプルなルールに分けて登録してください。'

            return Response({
                'success': False,
                'error': error_msg,
                'dates': [],
                'count': 0,
            }, status=status.HTTP_400_BAD_REQUEST)

        except Exception as exc:
            return Response({
                'success': False,
                'error': f'予期しないエラーが発生しました: {str(exc)}',
                'dates': [],
                'count': 0,
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
