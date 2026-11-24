"""Django シグナル: 集会承認・LT/特別回承認時に TweetQueue へ自動追加。

当日の発表（開始前）も含め、発表ごとに個別告知を 1 回だけキューする。
"""

import logging

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from community.models import Community
from event.models import EventDetail
from twitter.scheduling import default_scheduled_at
from twitter.services.announcement_eligibility import (
    EVENT_STARTED_SKIP_MESSAGE,
    PRESERVED_MISSKEY_STATUSES,
    RETRACTED_PRESENTATION_SKIP_MESSAGE,
    revocable_unposted_queue_q,
)
from twitter.services.daily_reminder import PRESENTATION_DETAIL_TYPES
from twitter.services import tweet_generation
from twitter.services.tweet_generation import sync_slide_share_queue_image

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Community)
def track_community_status_change(sender, instance, **kwargs):
    """Community の旧ステータスを保持する。"""
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
    """EventDetail の旧値を保持する。

    保存前の値は event・ta_hub のシグナルと共有する（``EventDetail.previous_values``。保存ごとに 1 回だけ読む）。
    新規や削除直後など旧値が無い時は空の dict になり、差分なしとして続行する。
    """
    old = instance.previous_values()
    instance._old_status = old.get('status')
    instance._old_slide_url = old.get('slide_url') or ""
    instance._old_youtube_url = old.get('youtube_url') or ""
    instance._old_slide_file = old.get('slide_file') or ""
    instance._old_speaker = old.get('speaker') or ""
    instance._old_theme = old.get('theme') or ""
    instance._old_start_time = old.get('start_time')
    instance._old_detail_type = old.get('detail_type')
    instance._old_event_id = old.get('event_id')
    instance._old_event_date = old.get('event__date')
    instance._old_deleted_at = old.get('deleted_at')

@receiver(post_save, sender=Community)
def queue_new_community_tweet(sender, instance, created, **kwargs):
    """Community が承認された時にツイートキューに追加する。"""
    try:
        _queue_new_community_tweet(instance, created)
    except Exception:
        logger.exception("Failed to queue new community tweet for %s", instance.pk)


def _queue_new_community_tweet(instance, created):
    from twitter.models import TweetQueue
    from event.models import Event

    if not instance.is_listed:
        return

    old_status = getattr(instance, "_old_status", None)
    if instance.status != "approved" or old_status == "approved":
        return

    if TweetQueue.objects.filter(community=instance, tweet_type="new_community").exists():
        return

    first_event = (
        Event.objects.filter(community=instance, date__gte=timezone.localdate())
        .order_by("date", "start_time")
        .first()
    )

    queue_item = TweetQueue.objects.create(
        tweet_type="new_community",
        community=instance,
        event=first_event,
        scheduled_at=default_scheduled_at(tweet_type='new_community', event=first_event),
    )
    logger.info("Queued new community tweet: %s", instance.name)

    tweet_generation.start_tweet_generation(queue_item)


@receiver(post_save, sender=EventDetail)
def queue_slide_share_tweet(sender, instance, created, **kwargs):
    """スライド/記事が初めてアップロードされた時にツイートキューに追加する。"""
    try:
        _queue_slide_share_tweet(instance, created)
    except Exception:
        logger.exception("Failed to queue slide share tweet for EventDetail %s", instance.pk)


def _queue_slide_share_tweet(instance, created):
    from twitter.models import TweetQueue
    from twitter.tweet_generator import get_tweet_image_url

    if instance.detail_type not in PRESENTATION_DETAIL_TYPES:
        return

    if instance.status != "approved":
        return

    if instance.event.date >= timezone.localdate():
        return

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

    should_notify_slide_webhook = slide_newly_set or slide_file_newly_set
    if should_notify_slide_webhook:
        from event.notifications import notify_slide_material_published

        notify_slide_material_published(instance)

    existing_queue = TweetQueue.objects.filter(
        event_detail=instance, tweet_type="slide_share",
    ).order_by('created_at', 'pk').first()
    if existing_queue:
        sync_slide_share_queue_image(instance)
        return

    queue_item = TweetQueue.objects.create(
        tweet_type="slide_share",
        community=instance.event.community,
        event=instance.event,
        event_detail=instance,
        scheduled_at=default_scheduled_at(tweet_type='slide_share', event=instance.event),
    )
    image_url = get_tweet_image_url(queue_item)
    if image_url:
        queue_item.image_url = image_url
        queue_item.save(update_fields=['image_url'])
    logger.info(
        "Queued slide share tweet: %s - %s", instance.speaker, instance.theme,
    )

    tweet_generation.start_tweet_generation(queue_item)


@receiver(post_save, sender=EventDetail)
def queue_event_detail_tweet(sender, instance, created, **kwargs):
    """LT/特別回の EventDetail に応じてキューを更新する。"""
    try:
        _queue_event_detail_tweet(instance, created)
    except Exception:
        logger.exception("Failed to queue event detail tweet for EventDetail %s", instance.pk)


def _has_event_started(event, now=None) -> bool:
    """当日イベントの開始時刻を過ぎているか返す。"""
    local_now = timezone.localtime(now or timezone.now())
    if event.date < local_now.date():
        return True
    if event.date > local_now.date():
        return False
    return local_now.time() >= event.start_time


def _delete_unposted_presentation_queues(instance, tweet_types=('lt', 'special')) -> int:
    """未投稿の発表告知キューを取り消す。Misskey 送信履歴がある行は CASCADE 削除せず保持する。"""
    from twitter.models import TweetQueue

    if not instance.pk:
        return 0
    base_qs = TweetQueue.objects.filter(
        event_detail=instance,
        tweet_type__in=tweet_types,
    ).filter(
        revocable_unposted_queue_q(),
    )
    preserve_ids = list(
        base_qs.filter(misskey_note__status__in=PRESERVED_MISSKEY_STATUSES)
        .values_list('pk', flat=True)
    )
    delete_ids = list(
        base_qs.exclude(misskey_note__status__in=PRESERVED_MISSKEY_STATUSES)
        .values_list('pk', flat=True)
    )
    preserved_skipped = 0
    if preserve_ids:
        preserved_skipped = TweetQueue.objects.filter(pk__in=preserve_ids).update(
            status='skipped',
            generated_text='',
            error_message=RETRACTED_PRESENTATION_SKIP_MESSAGE,
            generation_token='',
        )
    deleted = 0
    if delete_ids:
        deleted, _ = TweetQueue.objects.filter(pk__in=delete_ids).delete()
    return deleted + preserved_skipped


def _reuse_preserved_presentation_queue(instance, tweet_type: str):
    """取り下げ・削除時にスキップ保持したキューを再承認・復元・内容変更時に再利用する。"""
    from twitter.models import MisskeyNote, TweetQueue

    preserved = (
        TweetQueue.objects.filter(
            event_detail=instance,
            tweet_type=tweet_type,
            status='skipped',
            error_message=RETRACTED_PRESENTATION_SKIP_MESSAGE,
        )
        .order_by('-created_at', '-pk')
        .first()
    )
    if preserved is None:
        return None
    now_ts = timezone.now()
    preserved.community = instance.event.community
    preserved.event = instance.event
    preserved.scheduled_at = default_scheduled_at(tweet_type=tweet_type, event=instance.event)
    preserved.status = 'generating'
    preserved.generated_text = ''
    preserved.error_message = ''
    preserved.generation_token = ''
    preserved.save(
        update_fields=[
            'community',
            'event',
            'scheduled_at',
            'status',
            'generated_text',
            'error_message',
            'generation_token',
        ],
    )
    MisskeyNote.objects.filter(
        tweet_queue=preserved,
        status='skipped',
    ).update(
        status='pending',
        error_message='',
        attempts=0,
        updated_at=now_ts,
    )
    logger.info("Reused preserved %s tweet %d: %s - %s", tweet_type, preserved.pk, instance.speaker, instance.theme)
    tweet_generation.start_tweet_generation(preserved)
    return preserved


def sync_presentation_tweet_for_restored_detail(instance) -> None:
    """一括復元など pre_save/post_save を通らない復元時に発表告知キューを同期する。"""
    try:
        instance._old_status = None
        instance._old_deleted_at = timezone.now()
        _queue_event_detail_tweet(instance, created=False)
    except Exception:
        logger.exception("Failed to sync presentation tweet queue for restored EventDetail %s", instance.pk)


def _skip_unposted_presentation_queues_for_started_event(instance, tweet_types=('lt', 'special')) -> int:
    """開始済みイベントに変わった発表の未投稿キューを復帰可能な skipped 状態で保持する。"""
    from twitter.models import TweetQueue

    if not instance.pk:
        return 0
    return TweetQueue.objects.filter(
        event_detail=instance,
        tweet_type__in=tweet_types,
        status__in=('generating', 'generation_failed', 'ready'),
    ).update(
        status='skipped',
        generated_text='',
        error_message=EVENT_STARTED_SKIP_MESSAGE,
        generation_token='',
    )


def _queue_event_detail_tweet(instance, created):
    from twitter.models import TweetQueue

    if (
        instance.deleted_at is not None
        or instance.detail_type not in PRESENTATION_DETAIL_TYPES
        or instance.status != "approved"
    ):
        deleted = _delete_unposted_presentation_queues(instance)
        if deleted:
            logger.info("Deleted %d unposted presentation tweet(s) for detail %s", deleted, instance.pk)
        return

    if _has_event_started(instance.event):
        skipped = _skip_unposted_presentation_queues_for_started_event(instance)
        if skipped:
            logger.info("Skipped %d unposted presentation tweet(s) for started event detail %s", skipped, instance.pk)
        return

    old_status = getattr(instance, "_old_status", None)
    old_detail_type = getattr(instance, "_old_detail_type", None)
    old_event_id = getattr(instance, "_old_event_id", None)
    old_deleted_at = getattr(instance, "_old_deleted_at", None)
    tweet_type = "lt" if instance.detail_type == "LT" else "special"
    other_type = "special" if tweet_type == "lt" else "lt"
    deleted_other = _delete_unposted_presentation_queues(instance, tweet_types=(other_type,))
    if deleted_other:
        logger.info("Deleted %d stale %s tweet(s) after type change", deleted_other, other_type)

    if not created and old_status == "approved":
        old_speaker = getattr(instance, "_old_speaker", "")
        old_theme = getattr(instance, "_old_theme", "")
        new_speaker = instance.speaker or ""
        new_theme = instance.theme or ""

        if (
            old_detail_type == instance.detail_type
            and old_event_id == instance.event_id
            and old_speaker == new_speaker
            and old_theme == new_theme
        ):
            if old_deleted_at is None:
                return
            if _reuse_preserved_presentation_queue(instance, tweet_type) is not None:
                return
            if TweetQueue.objects.filter(event_detail=instance, tweet_type=tweet_type).exists():
                return
        else:
            deleted = _delete_unposted_presentation_queues(instance, tweet_types=(tweet_type,))
            if deleted:
                logger.info("Deleted %d unposted %s tweet(s) for regeneration", deleted, tweet_type)
            if _reuse_preserved_presentation_queue(instance, tweet_type) is not None:
                return

    else:
        if _reuse_preserved_presentation_queue(instance, tweet_type) is not None:
            return
        if TweetQueue.objects.filter(event_detail=instance, tweet_type=tweet_type).exists():
            return

    queue_item = TweetQueue.objects.create(
        tweet_type=tweet_type,
        community=instance.event.community,
        event=instance.event,
        event_detail=instance,
        scheduled_at=default_scheduled_at(tweet_type=tweet_type, event=instance.event),
    )
    logger.info("Queued %s tweet: %s - %s", tweet_type, instance.speaker, instance.theme)

    tweet_generation.start_tweet_generation(queue_item)


@receiver(post_delete, sender=EventDetail)
def cleanup_presentation_queues_on_delete(sender, instance, **kwargs):
    """発表削除時に未投稿の個別告知キューを取り消す。"""
    try:
        deleted = _delete_unposted_presentation_queues(instance)
        if deleted:
            logger.info(
                "Deleted %d unposted presentation tweet(s) after EventDetail delete %s",
                deleted,
                instance.pk,
            )
    except Exception:
        logger.exception("Failed to cleanup presentation queues after EventDetail delete %s", instance.pk)
