"""Misskey 投稿を X の投稿結果から独立して処理する。"""

from datetime import timedelta

import structlog
from django.db import transaction
from django.utils import timezone

from twitter import misskey_api
from twitter.misskey_text import to_misskey_text
from twitter.models import MisskeyNote
from twitter.notifications import notify_misskey_post_failure
from twitter.x_api import _failure_result

from .announcement_eligibility import event_date_skip_reason

logger = structlog.get_logger(__name__)
MAX_ATTEMPTS = 3
SCHEDULE_EXPIRY_HOURS = 24


def _log_post(note: MisskeyNote, http_status=None) -> None:
    """本文や認証値を含めず投稿結果を集計可能にする。"""
    log = logger.error if note.status == 'failed' else (
        logger.warning if note.error_message and note.status == 'pending' else logger.info
    )
    log(
        'misskey_post', queue_id=note.tweet_queue_id,
        tweet_type=note.tweet_queue.tweet_type,
        status=note.status, http_status=http_status,
    )


def process_pending_notes(*, now=None) -> None:
    """期限切れを除外し、pending を Cron ごとに最大 1 件投稿する。"""
    if not misskey_api.is_misskey_enabled():
        return
    current = now or timezone.now()
    expiry_threshold = current - timedelta(hours=SCHEDULE_EXPIRY_HOURS)

    # 行ロックで重複 Cron による同時投稿を防ぐ。X 側の状態は更新しない。
    with transaction.atomic():
        pending = MisskeyNote.objects.select_for_update().filter(status='pending').select_related(
            'tweet_queue__community', 'tweet_queue__event',
        ).order_by('tweet_queue__scheduled_at', 'pk')
        for note in pending:
            queue_item = note.tweet_queue
            if queue_item.scheduled_at < expiry_threshold or queue_item.status == 'skipped':
                note.status = 'skipped'
                note.error_message = '予約期限切れまたは告知対象外のため投稿をスキップ'
                note.save(update_fields=['status', 'error_message'])
                _log_post(note)
                continue
            if queue_item.scheduled_at > current:
                continue

            skip_reason = event_date_skip_reason(queue_item, today=timezone.localdate(current))
            if skip_reason:
                note.status = 'skipped'
                note.error_message = f'イベント日の投稿対象外のため投稿をスキップ ({skip_reason})'
                note.save(update_fields=['status', 'error_message'])
                _log_post(note)
                continue

            try:
                result = misskey_api.post_note(
                    to_misskey_text(queue_item.generated_text), image_url=queue_item.image_url or None,
                )
            except Exception as error:
                # 予期しないクライアント例外も試行上限に含め、X の結果には影響させない。
                logger.error('Misskey client raised exception', error_type=type(error).__name__)
                result = _failure_result(error_body='Misskey クライアントで例外が発生')
            note.attempts += 1
            if result['ok']:
                note.status = 'posted'
                note.note_id = result['data']['id']
                note.posted_at = current
                note.error_message = ''
            else:
                note.status = 'failed' if note.attempts >= MAX_ATTEMPTS else 'pending'
                note.error_message = (
                    f"Misskey 投稿に失敗 (status={result.get('status_code')}): "
                    f"{(result.get('error_body') or '')[:300]}"
                )
            note.save(update_fields=[
                'status', 'attempts', 'note_id', 'posted_at', 'error_message',
            ])
            _log_post(note, result.get('status_code'))
            if not result['ok']:
                notify_misskey_post_failure(queue_item, result)
            break
