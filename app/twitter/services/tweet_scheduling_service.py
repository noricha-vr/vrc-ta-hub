"""予約ポストの生成リトライと投稿処理を扱うサービス."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Protocol

from django.db import connections, models
from django.utils import timezone

from twitter.db import run_with_db_reconnect
from twitter.misskey_api import is_misskey_enabled
from twitter.models import MisskeyNote, TweetQueue
from twitter.notifications import notify_tweet_post_failure
from twitter.tweet_generator import get_generator, get_tweet_image_url, validate_tweet_text
from twitter.x_api import PostTweetResult

from .announcement_eligibility import (
    EVENT_DATE_PASSED_SKIP_MESSAGE,
    EVENT_STARTED_SKIP_MESSAGE,
    RETRACTED_PRESENTATION_SKIP_MESSAGE,
    SCHEDULE_EXPIRED_SKIP_REASON,
    UNLISTED_COMMUNITY_SKIP_MESSAGE,
    event_date_skip_reason,
    presentation_detail_skip_reason,
    unlisted_community_skip_reason,
)
from .media_service import upload_media_to_x
from .misskey_service import process_pending_notes
from .x_api_service import post_tweet_to_x

logger = logging.getLogger(__name__)

RETRY_THRESHOLD_HOURS = 1
SCHEDULE_EXPIRY_HOURS = 24


class PostTweetCallable(Protocol):
    """ポスト投稿依存をテストから差し替えるための呼び出し型."""

    def __call__(self, text: str, media_ids: list[str] | None = None) -> PostTweetResult:
        """テキストとメディアIDでポストを投稿する."""


class UploadMediaCallable(Protocol):
    """メディアアップロード依存をテストから差し替えるための呼び出し型."""

    def __call__(self, image_url: str) -> str | None:
        """画像URLをアップロードして media_id を返す."""


class FailureNotifier(Protocol):
    """投稿失敗通知依存をテストから差し替えるための呼び出し型."""

    def __call__(self, queue_item: TweetQueue, result: PostTweetResult) -> None:
        """投稿失敗時の通知を送信する."""


class RetryGenerationCallable(Protocol):
    """生成リトライ処理をテストから差し替えるための呼び出し型."""

    def __call__(self, queue_item: TweetQueue) -> None:
        """TweetQueue の生成リトライを実行する."""


class CloseConnectionsCallable(Protocol):
    """DB接続解放処理をテストから差し替えるための呼び出し型."""

    def __call__(self) -> None:
        """全DB接続を解放する."""


def retry_generation(queue_item: TweetQueue) -> None:
    """生成失敗キューのテキスト生成を同期的にリトライする."""
    try:
        generator = get_generator(queue_item.tweet_type)
        text = generator(queue_item) if generator else None
    except Exception:
        logger.exception("Retry generation raised exception for queue %d", queue_item.pk)
        queue_item.status = 'generation_failed'
        queue_item.error_message = 'リトライ中に例外が発生'
        run_with_db_reconnect(
            queue_item.save,
            context=f"retry_generation_exception queue={queue_item.pk}",
        )
        return

    if text:
        queue_item.generated_text = text
        queue_item.status = 'ready'
        queue_item.error_message = ''

        if queue_item.tweet_type == 'slide_share' or not queue_item.image_url:
            image_url = get_tweet_image_url(queue_item)
            if image_url:
                queue_item.image_url = image_url

        run_with_db_reconnect(
            queue_item.save,
            context=f"retry_generation_success queue={queue_item.pk}",
        )
        logger.info("Retry generation succeeded for queue %d", queue_item.pk)
    else:
        queue_item.status = 'generation_failed'
        queue_item.error_message = 'リトライ生成にも失敗'
        run_with_db_reconnect(
            queue_item.save,
            context=f"retry_generation_failed queue={queue_item.pk}",
        )
        logger.error("Retry generation failed for queue %d", queue_item.pk)


def retry_generation_async(
    queue_id: int,
    *,
    retry_func: RetryGenerationCallable = retry_generation,
    close_connections_func: CloseConnectionsCallable = connections.close_all,
) -> None:
    """バックグラウンドスレッドで再生成し、終了時にDB接続を解放する."""
    try:
        try:
            queue_item = run_with_db_reconnect(
                lambda: TweetQueue.objects.select_related(
                    'community', 'event', 'event_detail',
                ).get(pk=queue_id),
                context=f"retry_generation_fetch queue={queue_id}",
            )
        except TweetQueue.DoesNotExist:
            logger.error("TweetQueue %d not found for retry generation", queue_id)
            return

        retry_func(queue_item)
    except Exception:
        logger.exception("Async retry generation failed for queue %d", queue_id)
    finally:
        close_connections_func()


def _record_post_failure(
    queue_item: TweetQueue,
    *,
    error_message: str,
    failure_status: str | None,
    notify_failure_func: FailureNotifier,
    result: PostTweetResult,
    error_code: str,
    context: str,
) -> dict[str, object]:
    """投稿失敗または事前ブロックの状態を保存して failure 通知を送る."""
    if failure_status is not None:
        queue_item.status = failure_status
    queue_item.error_message = error_message
    failure_update_fields = ['error_message']
    if failure_status is not None:
        failure_update_fields.append('status')
    run_with_db_reconnect(
        lambda: queue_item.save(update_fields=failure_update_fields),
        context=context,
    )
    notify_failure_func(queue_item, result)
    return {
        "id": queue_item.pk, "status": "failed", "error": error_code,
    }


def post_tweet_queue_item(
    queue_item: TweetQueue,
    *,
    failure_status: str | None = 'failed',
    post_tweet_func: PostTweetCallable = post_tweet_to_x,
    upload_media_func: UploadMediaCallable = upload_media_to_x,
    notify_failure_func: FailureNotifier = notify_tweet_post_failure,
) -> dict[str, object]:
    """TweetQueue 1件を X API に投稿し、結果を保存してから返す."""
    if (
        queue_item.tweet_type in ('lt', 'special')
        and 'contains_url' in validate_tweet_text(queue_item.generated_text, forbid_urls=True)
    ):
        logger.warning(
            "Blocked %s tweet containing URL before X API call (queue=%d)",
            queue_item.tweet_type,
            queue_item.pk,
        )
        block_message = 'URLを含むためX投稿をブロックしました（再生成してください）'
        return _record_post_failure(
            queue_item,
            error_message=block_message,
            failure_status=failure_status,
            notify_failure_func=notify_failure_func,
            result={
                "ok": False,
                "data": None,
                "status_code": None,
                "error_body": block_message,
            },
            error_code="contains_url",
            context=f"post_tweet_queue_item_url_blocked queue={queue_item.pk}",
        )

    media_ids = None
    if queue_item.image_url:
        media_id = upload_media_func(queue_item.image_url)
        if media_id:
            media_ids = [media_id]

    result = post_tweet_func(queue_item.generated_text, media_ids=media_ids)

    if result["ok"]:
        queue_item.status = 'posted'
        queue_item.tweet_id = (result["data"] or {}).get('id', '')
        queue_item.posted_at = timezone.now()
        queue_item.error_message = ''
        run_with_db_reconnect(
            lambda: queue_item.save(
                update_fields=['status', 'tweet_id', 'posted_at', 'error_message'],
            ),
            context=f"post_tweet_queue_item_save_success queue={queue_item.pk}",
        )
        return {
            "id": queue_item.pk, "status": "posted", "tweet_id": queue_item.tweet_id,
        }

    status_code = result.get("status_code")
    error_message = (
        f'X API投稿に失敗 (status={status_code})' if status_code else 'X API投稿に失敗'
    )
    error_body = result.get("error_body")
    if error_body:
        error_message = f"{error_message}: {error_body[:300]}"
    return _record_post_failure(
        queue_item,
        error_message=error_message,
        failure_status=failure_status,
        notify_failure_func=notify_failure_func,
        result=result,
        error_code="post_failed",
        context=f"post_tweet_queue_item_save_failure queue={queue_item.pk}",
    )


def _process_scheduled_tweets_for_x(
    *,
    now=None,
    post_tweet_func: PostTweetCallable = post_tweet_to_x,
    upload_media_func: UploadMediaCallable = upload_media_to_x,
    notify_failure_func: FailureNotifier = notify_tweet_post_failure,
) -> dict[str, object]:
    """期限切れ・生成リトライ・投稿対象キューを順に処理する."""
    created_count = 0
    current = now or timezone.now()
    expiry_threshold = current - timedelta(hours=SCHEDULE_EXPIRY_HOURS)

    overdue_items = run_with_db_reconnect(
        lambda: list(
            TweetQueue.objects.filter(
                status__in=('generating', 'generation_failed', 'ready'),
                scheduled_at__lt=expiry_threshold,
            ).select_related('community', 'event', 'event_detail'),
        ),
        context="post_scheduled_tweets_fetch_overdue",
    )

    results: list[dict[str, object]] = []
    for item in overdue_items:
        item.status = 'skipped'
        item.error_message = SCHEDULE_EXPIRED_SKIP_REASON
        run_with_db_reconnect(
            lambda: item.save(update_fields=['status', 'error_message']),
            context=f"post_scheduled_tweets_skip_expired queue={item.pk}",
        )
        results.append({
            "id": item.pk, "status": "skipped", "reason": "schedule_expired",
        })
        logger.info("Skipped expired scheduled tweet for queue %d", item.pk)

    retry_threshold = current - timedelta(hours=RETRY_THRESHOLD_HOURS)
    retry_items = run_with_db_reconnect(
        lambda: list(
            TweetQueue.objects.filter(
                (
                    models.Q(status='generation_failed')
                    | models.Q(status='generating', created_at__lt=retry_threshold)
                ),
            ).select_related('community', 'event', 'event_detail'),
        ),
        context="post_scheduled_tweets_fetch_retry",
    )
    retried_count = len(retry_items)

    for item in retry_items:
        retry_generation(item)

    ready_items = run_with_db_reconnect(
        lambda: list(
            TweetQueue.objects.filter(
                status='ready',
                scheduled_at__lte=current,
            ).select_related(
                'community', 'event', 'event_detail',
            ).order_by('scheduled_at', 'pk'),
        ),
        context="post_scheduled_tweets_fetch_ready",
    )
    posted_attempted = False
    for queue_item in ready_items:
        dispatch_now = now or timezone.now()
        skip_status = None
        skip_message = ''
        skip_code = None

        if unlisted_community_skip_reason(queue_item):
            skip_status = 'skipped'
            skip_message = UNLISTED_COMMUNITY_SKIP_MESSAGE
            skip_code = 'unlisted_community'
        else:
            date_reason = event_date_skip_reason(queue_item, now=dispatch_now)
            detail_reason = presentation_detail_skip_reason(queue_item)
            if date_reason == 'daily_reminder_retired':
                skip_status = 'skipped'
                skip_message = '当日リマインドは廃止されたため投稿スキップ'
                skip_code = 'daily_reminder_retired'
            elif queue_item.tweet_type in ('lt', 'special'):
                if date_reason == 'event_date_passed':
                    skip_status = 'failed'
                    skip_message = EVENT_DATE_PASSED_SKIP_MESSAGE
                    skip_code = 'event_date_passed'
                elif date_reason == 'event_started':
                    skip_status = 'skipped'
                    skip_message = EVENT_STARTED_SKIP_MESSAGE
                    skip_code = 'event_started'
                elif detail_reason:
                    skip_status = 'skipped'
                    skip_message = RETRACTED_PRESENTATION_SKIP_MESSAGE
                    skip_code = detail_reason

        if skip_status is not None:
            queue_item.status = skip_status
            queue_item.error_message = skip_message
            run_with_db_reconnect(
                lambda: queue_item.save(update_fields=['status', 'error_message']),
                context=f"post_scheduled_tweets_skip queue={queue_item.pk} reason={skip_code}",
            )
            results.append({
                "id": queue_item.pk, "status": "skipped", "reason": skip_code,
            })
            logger.info("Skipped %s tweet for queue %d (%s)", queue_item.tweet_type, queue_item.pk, skip_code)
            continue

        if is_misskey_enabled():
            try:
                run_with_db_reconnect(
                    lambda: MisskeyNote.objects.get_or_create(tweet_queue=queue_item),
                    context=f"prepare_misskey_note queue={queue_item.pk}",
                )
            except Exception:
                logger.exception("Failed to prepare Misskey note for queue %d", queue_item.pk)
                # ready を維持し、次回 Cron で配信行の作成から再試行する。
                break

        result = post_tweet_queue_item(
            queue_item,
            failure_status='failed',
            post_tweet_func=post_tweet_func,
            upload_media_func=upload_media_func,
            notify_failure_func=notify_failure_func,
        )
        results.append(result)
        if result["status"] == "posted":
            logger.info("Tweet posted for queue %d: %s", queue_item.pk, queue_item.tweet_id)
        else:
            logger.warning("Tweet post failed for queue %d", queue_item.pk)

        posted_attempted = True
        break

    return {
        "created": created_count,
        "retried": retried_count,
        "processed": len(results),
        "posted_attempted": posted_attempted,
        "results": results,
    }


def process_scheduled_tweets(
    *,
    now=None,
    post_tweet_func: PostTweetCallable = post_tweet_to_x,
    upload_media_func: UploadMediaCallable = upload_media_to_x,
    notify_failure_func: FailureNotifier = notify_tweet_post_failure,
) -> dict[str, object]:
    """X を処理し、失敗・例外・対象なしでも独立した Misskey 段を実行する。"""
    try:
        return _process_scheduled_tweets_for_x(
            now=now, post_tweet_func=post_tweet_func,
            upload_media_func=upload_media_func, notify_failure_func=notify_failure_func,
        )
    finally:
        if is_misskey_enabled():
            try:
                process_pending_notes(now=now)
            except Exception:
                logger.exception("Failed to process pending Misskey notes")
