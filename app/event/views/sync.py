import logging

from django.http import HttpResponse
from django.utils import timezone

from event.models import Event
from event.sync_to_google import DatabaseToGoogleSync
from website.settings import REQUEST_TOKEN

logger = logging.getLogger(__name__)


def sync_calendar_events(request):
    """データベースからGoogleカレンダーへの同期（重複防止機能付き）"""
    if request.method != 'GET':
        return HttpResponse("Invalid request method.", status=405)

    # Get the Request-Token
    request_token = request.headers.get('Request-Token', '')

    # セキュリティチェック（必要に応じて）
    if request_token != REQUEST_TOKEN:
        return HttpResponse("Unauthorized", status=401)

    try:
        logger.info('=' * 80)
        logger.info('データベースからGoogleカレンダーへの同期開始')
        logger.info(f'同期開始時刻: {timezone.now()}')
        logger.info('=' * 80)

        try:
            months_ahead = int(request.GET.get('months', 3))
        except (TypeError, ValueError):
            return HttpResponse("Invalid months parameter.", status=400)

        if months_ahead < 1 or months_ahead > 12:
            return HttpResponse("months must be between 1 and 12.", status=400)
        logger.info(f'同期対象期間: {months_ahead}ヶ月先まで')

        # 重複防止機能付きの同期処理を実行
        sync = DatabaseToGoogleSync()
        stats = sync.sync_all_communities(months_ahead=months_ahead)

        # 同期結果のサマリー
        logger.info('=' * 80)
        logger.info('同期完了サマリー:')
        logger.info(f"  現在のDBイベント総数: {Event.objects.count()}件")
        logger.info(f"  新規作成: {stats['created']}件")
        logger.info(f"  更新: {stats['updated']}件")
        logger.info(f"  削除: {stats['deleted']}件")
        logger.info(f"  エラー: {stats['errors']}件")

        # 重複防止が機能した場合のログ
        if stats.get('duplicate_prevented', 0) > 0:
            logger.info(f"  重複防止により更新に切り替え: {stats['duplicate_prevented']}件")

        logger.info(f'同期終了時刻: {timezone.now()}')
        logger.info('=' * 80)

        # レスポンスメッセージ
        response_message = (
            f"Calendar events synchronized successfully. "
            f"Created: {stats['created']}, Updated: {stats['updated']}, "
            f"Skipped: {stats.get('skipped', 0)}, "
            f"Deleted: {stats['deleted']}, Errors: {stats['errors']}"
        )

        if stats.get('duplicate_prevented', 0) > 0:
            response_message += f", Duplicate prevented: {stats['duplicate_prevented']}"

        return HttpResponse(response_message, status=200)

    except Exception as e:
        logger.error('=' * 80)
        logger.error(f"同期失敗: {str(e)}")
        logger.error('=' * 80, exc_info=True)
        return HttpResponse("Failed to sync calendar events. Check server logs for details.", status=500)
