"""TweetQueue 本文生成の非同期実行ヘルパー。

シグナルレシーバから呼ばれ、バックグラウンドスレッドで生成した結果を
generation_token の compare-and-set で TweetQueue へ書き戻す。
"""

import logging
import sys
import threading
import uuid

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError

from twitter.db import run_with_db_reconnect

logger = logging.getLogger(__name__)


def _should_skip_tweet_generation_thread() -> bool:
    """テスト実行時は TweetQueue 生成後のバックグラウンドスレッドだけ抑制する。"""
    if getattr(settings, 'ENABLE_TWEET_GENERATION_THREADS_IN_TESTS', False):
        return False

    is_test_run = getattr(settings, 'TESTING', False) or 'test' in sys.argv
    if not is_test_run:
        return False

    # Thread を明示 patch するテストは、この経路自体を検証している。
    return getattr(threading.Thread, '__module__', 'threading') == 'threading'


def _save_generation_failure(queue_id: int, generation_token: str, error_message: str) -> None:
    from twitter.models import TweetQueue

    if generation_token:
        updated = run_with_db_reconnect(
            lambda: TweetQueue.objects.filter(
                pk=queue_id,
                generation_token=generation_token,
            ).update(status='generation_failed', error_message=error_message),
            context=f"generate_tweet_failed queue={queue_id}",
        )
        if not updated:
            logger.info("Ignored stale failed tweet generation for queue %d", queue_id)
        return

    item = run_with_db_reconnect(
        lambda: TweetQueue.objects.get(pk=queue_id),
        context=f"generate_tweet_failed_fetch queue={queue_id}",
    )
    item.status = 'generation_failed'
    item.error_message = error_message
    run_with_db_reconnect(
        item.save,
        context=f"generate_tweet_failed queue={queue_id}",
    )


def _generate_tweet_async(queue_id: int, generation_token: str = "") -> None:
    """バックグラウンドスレッドでツイートテキストを生成する。"""
    from django.db import connections

    try:
        from twitter.models import TweetQueue
        from twitter.tweet_generator import get_generator, get_tweet_image_url

        try:
            queue_item = run_with_db_reconnect(
                lambda: TweetQueue.objects.select_related(
                    'community', 'event', 'event_detail',
                ).get(pk=queue_id),
                context=f"generate_tweet_fetch queue={queue_id}",
            )
        except TweetQueue.DoesNotExist:
            logger.error("TweetQueue %d not found", queue_id)
            return

        generator = get_generator(queue_item.tweet_type)
        text = generator(queue_item) if generator else None

        if not text:
            _save_generation_failure(queue_id, generation_token, 'テキスト生成に失敗')
            return

        image_url = get_tweet_image_url(queue_item)

        if generation_token:
            update_values = {
                'generated_text': text,
                'status': 'ready',
                'error_message': '',
            }
            if image_url:
                update_values['image_url'] = image_url
            updated = run_with_db_reconnect(
                lambda: TweetQueue.objects.filter(
                    pk=queue_id,
                    generation_token=generation_token,
                ).update(**update_values),
                context=f"generate_tweet_success queue={queue_id}",
            )
            if not updated:
                logger.info("Ignored stale tweet generation for queue %d", queue_id)
                return
        else:
            queue_item.generated_text = text
            if image_url:
                queue_item.image_url = image_url
            queue_item.status = 'ready'
            queue_item.error_message = ''
            run_with_db_reconnect(
                queue_item.save,
                context=f"generate_tweet_success queue={queue_id}",
            )
        logger.info("Tweet text generated for queue %d", queue_id)

    except Exception as e:
        logger.exception("Async tweet generation failed for queue %d", queue_id)
        try:
            _save_generation_failure(queue_id, generation_token, str(e)[:500])
        except (DatabaseError, ObjectDoesNotExist):
            logger.exception(
                "Failed to persist async tweet generation failure for queue %d",
                queue_id,
            )
    finally:
        connections.close_all()


def _start_tweet_generation(queue_item) -> None:
    """TweetQueue の本文生成をバックグラウンドで開始する。"""
    generation_token = uuid.uuid4().hex
    queue_item.generation_token = generation_token
    queue_item.save(update_fields=['generation_token'])

    if _should_skip_tweet_generation_thread():
        logger.debug("Skipped tweet generation thread in tests for queue %d", queue_item.pk)
        return

    thread = threading.Thread(
        target=_generate_tweet_async, args=(queue_item.pk, generation_token), daemon=True,
    )
    thread.start()


def sync_slide_share_queue_image(event_detail) -> None:
    """未投稿のスライド共有キュー画像を現在のサムネイル優先URLへ同期する。"""
    from twitter.models import TweetQueue
    from twitter.tweet_generator import get_tweet_image_url

    existing_queue = TweetQueue.objects.select_related(
        'community', 'event', 'event_detail',
    ).filter(
        event_detail=event_detail, tweet_type="slide_share",
    ).order_by('created_at', 'pk').first()
    if not existing_queue or existing_queue.status == 'posted':
        return

    image_url = get_tweet_image_url(existing_queue)
    if image_url and existing_queue.image_url != image_url:
        existing_queue.image_url = image_url
        existing_queue.save(update_fields=['image_url'])
