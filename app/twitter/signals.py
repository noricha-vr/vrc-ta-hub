"""Django シグナル: 集会承認・LT/特別回承認時に TweetQueue へ自動追加

シグナル発火時に TweetQueue を generating 状態で作成し、
バックグラウンドスレッドでテキスト生成を非同期実行する。
"""

import logging
import threading

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from community.models import Community
from event.models import EventDetail

logger = logging.getLogger(__name__)


def _generate_tweet_async(queue_id: int) -> None:
    """バックグラウンドスレッドでツイートテキストを生成する。

    生成成功時は status を ready に、失敗時は generation_failed に更新する。
    別スレッドで実行されるため、全例外をキャッチしてテストを妨げないようにし、
    終了時に DB 接続を確実にクローズする。
    """
    from django.db import connection

    try:
        from twitter.models import TweetQueue
        from twitter.tweet_generator import get_generator, get_poster_image_url

        try:
            queue_item = TweetQueue.objects.select_related(
                'community', 'event', 'event_detail',
            ).get(pk=queue_id)
        except TweetQueue.DoesNotExist:
            logger.error("TweetQueue %d not found", queue_id)
            return

        generator = get_generator(queue_item.tweet_type)
        text = generator(queue_item) if generator else None

        if not text:
            queue_item.status = 'generation_failed'
            queue_item.error_message = 'テキスト生成に失敗'
            queue_item.save()
            return

        queue_item.generated_text = text

        # 画像URL取得（ポスター画像がある場合）
        image_url = get_poster_image_url(queue_item.community)
        if image_url:
            queue_item.image_url = image_url

        queue_item.status = 'ready'
        queue_item.error_message = ''
        queue_item.save()
        logger.info("Tweet text generated for queue %d", queue_id)

    except Exception as e:
        logger.warning(
            "Async tweet generation failed for queue %d: %s", queue_id, e,
        )
        # ステータス更新を試みるが、DBロック時は静かに失敗する
        try:
            from twitter.models import TweetQueue

            item = TweetQueue.objects.get(pk=queue_id)
            item.status = 'generation_failed'
            item.error_message = str(e)[:500]
            item.save()
        except Exception:
            pass
    finally:
        connection.close()


@receiver(pre_save, sender=Community)
def track_community_status_change(sender, instance, **kwargs):
    """Community のステータス変更を追跡する。

    post_save で旧値を参照できるよう _old_status を instance に保持する。
    """
    if instance.pk:
        try:
            old = Community.objects.get(pk=instance.pk)
            instance._old_status = old.status
        except Community.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None


@receiver(pre_save, sender=EventDetail)
def track_event_detail_status_change(sender, instance, **kwargs):
    """EventDetail のステータス変更を追跡する。

    post_save で旧値を参照できるよう _old_status, _old_slide_url,
    _old_youtube_url を instance に保持する。
    """
    if instance.pk:
        try:
            old = EventDetail.objects.get(pk=instance.pk)
            instance._old_status = old.status
            instance._old_slide_url = old.slide_url or ""
            instance._old_youtube_url = old.youtube_url or ""
            instance._old_slide_file = str(old.slide_file) if old.slide_file else ""
        except EventDetail.DoesNotExist:
            instance._old_status = None
            instance._old_slide_url = ""
            instance._old_youtube_url = ""
            instance._old_slide_file = ""
    else:
        instance._old_status = None
        instance._old_slide_url = ""
        instance._old_youtube_url = ""
        instance._old_slide_file = ""


@receiver(post_save, sender=Community)
def queue_new_community_tweet(sender, instance, created, **kwargs):
    """Community が承認された時にツイートキューに追加する。

    pending -> approved への遷移時のみトリガーされる。
    同一 community の重複キューは作成しない。
    ツイートキューは補助機能のため、失敗しても本体の保存処理に影響させない。
    """
    try:
        _queue_new_community_tweet(instance, created)
    except Exception:
        logger.exception("Failed to queue new community tweet for %s", instance.pk)


def _queue_new_community_tweet(instance, created):
    # 遅延インポートで循環インポートを回避
    from twitter.models import TweetQueue

    old_status = getattr(instance, "_old_status", None)

    # approved 以外、または既に approved だった場合はスキップ
    if instance.status != "approved" or old_status == "approved":
        return

    # 重複チェック
    if TweetQueue.objects.filter(community=instance, tweet_type="new_community").exists():
        return

    # 初回イベントを取得
    from django.utils import timezone
    from event.models import Event

    first_event = (
        Event.objects.filter(community=instance, date__gte=timezone.now().date())
        .order_by("date", "start_time")
        .first()
    )

    queue_item = TweetQueue.objects.create(
        tweet_type="new_community",
        community=instance,
        event=first_event,
    )
    logger.info("Queued new community tweet: %s", instance.name)

    thread = threading.Thread(
        target=_generate_tweet_async, args=(queue_item.pk,), daemon=True,
    )
    thread.start()


@receiver(post_save, sender=EventDetail)
def queue_slide_share_tweet(sender, instance, created, **kwargs):
    """スライド/記事が初めてアップロードされた時にツイートキューに追加する。

    ツイートキューは補助機能のため、失敗しても本体の保存処理に影響させない。
    """
    try:
        _queue_slide_share_tweet(instance, created)
    except Exception:
        logger.exception("Failed to queue slide share tweet for EventDetail %s", instance.pk)


def _queue_slide_share_tweet(instance, created):
    """以下の条件をすべて満たす場合にキューを追加する:

    - slide_url, youtube_url, slide_file のいずれかが初めて設定された
    - status が approved（承認済み）
    - event.date が過去（発表日が終わっている）
    - 同じ event_detail に対して slide_share キューが未登録
    """
    from django.utils import timezone

    from twitter.models import TweetQueue

    # detail_type チェック（LT/SPECIAL のみ対象）
    if instance.detail_type not in ("LT", "SPECIAL"):
        return

    # 承認済みのみ対象
    if instance.status != "approved":
        return

    # 発表日が過去かチェック
    if instance.event.date >= timezone.now().date():
        return

    # slide_url, youtube_url, slide_file が初めて設定されたかチェック
    old_slide_url = getattr(instance, "_old_slide_url", "")
    old_youtube_url = getattr(instance, "_old_youtube_url", "")
    old_slide_file = getattr(instance, "_old_slide_file", "")
    new_slide_url = instance.slide_url or ""
    new_youtube_url = instance.youtube_url or ""
    new_slide_file = str(instance.slide_file) if instance.slide_file else ""

    slide_newly_set = not old_slide_url and new_slide_url
    youtube_newly_set = not old_youtube_url and new_youtube_url
    slide_file_newly_set = not old_slide_file and new_slide_file

    if not slide_newly_set and not youtube_newly_set and not slide_file_newly_set:
        return

    # 重複チェック
    if TweetQueue.objects.filter(
        event_detail=instance, tweet_type="slide_share",
    ).exists():
        return

    queue_item = TweetQueue.objects.create(
        tweet_type="slide_share",
        community=instance.event.community,
        event=instance.event,
        event_detail=instance,
    )
    logger.info(
        "Queued slide share tweet: %s - %s", instance.speaker, instance.theme,
    )

    thread = threading.Thread(
        target=_generate_tweet_async, args=(queue_item.pk,), daemon=True,
    )
    thread.start()


@receiver(post_save, sender=EventDetail)
def queue_event_detail_tweet(sender, instance, created, **kwargs):
    """LT/特別回の EventDetail が承認された時にツイートキューに追加する。

    ツイートキューは補助機能のため、失敗しても本体の保存処理に影響させない。
    """
    try:
        _queue_event_detail_tweet(instance, created)
    except Exception:
        logger.exception("Failed to queue event detail tweet for EventDetail %s", instance.pk)


def _queue_event_detail_tweet(instance, created):
    """以下の場合にキューを追加する:

    - 新規作成 (created=True) かつ status='approved'
    - 既存更新で _old_status != 'approved' から status='approved' に遷移

    detail_type が 'LT' or 'SPECIAL' の場合のみ。
    同一 event_detail の重複キューは作成しない。
    """
    from twitter.models import TweetQueue

    if instance.detail_type not in ("LT", "SPECIAL"):
        return

    if instance.status != "approved":
        return

    old_status = getattr(instance, "_old_status", None)

    # 既に approved だった場合はスキップ（新規作成時は old_status=None なので通過）
    if not created and old_status == "approved":
        return

    tweet_type = "lt" if instance.detail_type == "LT" else "special"

    # 重複チェック
    if TweetQueue.objects.filter(event_detail=instance, tweet_type=tweet_type).exists():
        return

    queue_item = TweetQueue.objects.create(
        tweet_type=tweet_type,
        community=instance.event.community,
        event=instance.event,
        event_detail=instance,
    )
    logger.info("Queued %s tweet: %s - %s", tweet_type, instance.speaker, instance.theme)

    thread = threading.Thread(
        target=_generate_tweet_async, args=(queue_item.pk,), daemon=True,
    )
    thread.start()
