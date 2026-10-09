"""記事化 OK の発表で動画・PDF・同意が変わった時に、記事の生成待ちの印を付ける。

フォーム・API・管理画面のどこから保存しても拾えるよう、保存のシグナルで判定する。
生成そのものは Cloud Scheduler から呼ぶエンドポイント（event.services.article_generation）が行う。
"""

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from event.models import EventDetail, youtube_video_id

# これらの列を含む保存だけ、印を付けるかを判定する
ARTICLE_TRIGGER_FIELDS = frozenset({'slide_file', 'youtube_url', 'article_consent'})


@receiver(pre_save, sender=EventDetail)
def remember_article_trigger_values(sender, instance, raw=False, update_fields=None, **kwargs):
    """保存前の動画・PDF・同意を退避する。自動生成の対象にならない保存では DB を読まない。

    保存前の値は ta_hub・twitter のシグナルと共有する（``EventDetail.previous_values``。保存ごとに 1 回だけ読む）。
    """
    instance._article_trigger_old = None
    if raw:
        return
    if update_fields is not None and not ARTICLE_TRIGGER_FIELDS & set(update_fields):
        return
    if not instance.can_auto_generate_article:
        return
    instance._article_trigger_old = instance.previous_values()


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
    """同意が OK に変わったか、YouTube 動画・PDF が新しく入った・変わったか。外しただけの時は含めない。

    動画は URL ではなく動画 ID で比べる（再生位置 ?t= の付け替えや Discord のリンクでは作り直さない）。
    """
    if old.get('article_consent') != EventDetail.ArticleConsent.OK:
        return True
    slide_name = instance.slide_file.name if instance.slide_file else ''
    if slide_name and slide_name != (old.get('slide_file') or ''):
        return True
    video_id = instance.video_id
    return bool(video_id) and video_id != youtube_video_id(old.get('youtube_url'))
