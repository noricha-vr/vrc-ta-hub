"""記事化 OK の発表で動画・PDF・同意が変わった時に、記事の生成待ちの印を付ける。

フォーム・API・管理画面のどこから保存しても拾えるよう、保存のシグナルで判定する。
生成そのものは Cloud Scheduler から呼ぶエンドポイント（event.services.article_generation）が行う。
"""

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from event.models import EventDetail

# これらの列を含む保存だけ、印を付けるかを判定する
ARTICLE_TRIGGER_FIELDS = frozenset({'slide_file', 'youtube_url', 'article_consent'})


@receiver(pre_save, sender=EventDetail)
def remember_article_trigger_values(sender, instance, raw=False, update_fields=None, **kwargs):
    """保存前の動画・PDF・同意を退避する。自動生成の対象にならない保存では DB を読まない。"""
    instance._article_trigger_old = None
    if raw:
        return
    if update_fields is not None and not ARTICLE_TRIGGER_FIELDS & set(update_fields):
        return
    if not instance.can_auto_generate_article:
        return
    if instance.pk is None:
        instance._article_trigger_old = {}
        return
    old = (
        EventDetail.all_objects.filter(pk=instance.pk)
        .values('slide_file', 'youtube_url', 'article_consent')
        .first()
    )
    instance._article_trigger_old = old or {}


@receiver(post_save, sender=EventDetail)
def request_article_generation(sender, instance, raw=False, **kwargs):
    """入力が新しくなり、記事が未生成か自動生成のままなら、生成待ちの印を付ける。

    ``save(update_fields=...)`` でも確実に残るよう、印は別の UPDATE で書き込む。
    """
    old = getattr(instance, '_article_trigger_old', None)
    instance._article_trigger_old = None
    if raw or old is None:
        return
    if not _article_inputs_changed(instance, old):
        return
    if instance.article_state() == EventDetail.ArticleState.MANUAL:
        return

    marked = {
        'article_generation_requested_at': timezone.now(),
        'article_generation_attempts': 0,
        'article_generation_last_error': '',
    }
    EventDetail.all_objects.filter(pk=instance.pk).update(**marked)
    # 同じインスタンスをもう一度 save() しても印が消えないよう、メモリ上の値も揃える
    for field_name, value in marked.items():
        setattr(instance, field_name, value)


def _article_inputs_changed(instance: EventDetail, old: dict) -> bool:
    """同意が OK に変わったか、動画・PDF が新しく入った・変わったか。外しただけの時は含めない。"""
    if old.get('article_consent') != EventDetail.ArticleConsent.OK:
        return True
    slide_name = instance.slide_file.name if instance.slide_file else ''
    if slide_name and slide_name != (old.get('slide_file') or ''):
        return True
    youtube_url = instance.youtube_url or ''
    return bool(youtube_url) and youtube_url != (old.get('youtube_url') or '')
