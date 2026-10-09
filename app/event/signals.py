"""発表（EventDetail）の保存に合わせた記事まわりの後処理。

- 記事の自動生成の対象になった・動画や PDF が変わった・記事を空にした時に、記事の生成待ちの印を付ける
  （生成そのものは Cloud Scheduler から呼ぶ event.services.article_generation が行う）
- 記事のタイトル（h1）か記事化の同意が変わった時に、関連一覧のキャッシュを消す

フォーム・API・管理画面のどこから保存しても拾えるよう、保存のシグナルで判定する。
保存前の値は ta_hub・twitter のシグナルと共有する（``EventDetail.previous_values``。保存ごとに 1 回だけ読む）。
"""

from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from event.models import (
    Event,
    EventDetail,
    article_generation_request_values,
    article_is_empty,
    related_event_details_cache_key,
)
from event.youtube_urls import youtube_video_id

# これらの列を含む保存だけ、保存前の値を退避して判定する。
# status / detail_type / deleted_at は、却下からの承認・種別の変更・論理削除からの復元で後から対象になるため。
# h1 / contents は、記事を空にした時に作り直すため（h1 は関連一覧のキャッシュの判定にも使う）
WATCHED_FIELDS = frozenset({
    'slide_file', 'youtube_url', 'article_consent', 'status', 'detail_type', 'deleted_at', 'h1', 'contents',
})


@receiver(pre_save, sender=EventDetail)
def remember_article_previous_values(sender, instance, raw=False, update_fields=None, **kwargs):
    """保存前の値を退避する（保存の後では DB が新しい値になるため、ここで読む）。"""
    instance._article_previous = None
    if raw:
        return
    if update_fields is not None and not WATCHED_FIELDS & set(update_fields):
        return
    instance._article_previous = instance.previous_values()


@receiver(post_save, sender=EventDetail)
def handle_article_changes(sender, instance, created=False, raw=False, update_fields=None, **kwargs):
    """関連一覧のキャッシュを消し、必要なら記事の生成待ちの印を付ける。"""
    old = getattr(instance, '_article_previous', None)
    instance._article_previous = None
    if raw or old is None:
        return
    _clear_related_cache_if_changed(instance, old, created)
    _request_article_generation_if_needed(instance, old, update_fields)


def _request_article_generation_if_needed(instance: EventDetail, old: dict, update_fields=None) -> None:
    """対象外から対象になった・動画や PDF が新しくなった・記事を空にした時に、記事が未生成か
    自動生成のままなら印を付ける。

    ``save(update_fields=...)`` でも確実に残るよう、印は別の UPDATE で書き込む。
    """
    if not instance.can_auto_generate_article:
        return
    if (
        _was_auto_generation_target(old)
        and not _article_inputs_changed(instance, old)
        and not _article_emptied(instance, old, update_fields)
    ):
        return
    if instance.article_state() == EventDetail.ArticleState.MANUAL:
        return

    marked = article_generation_request_values(timezone.now())
    EventDetail.all_objects.filter(pk=instance.pk).update(**marked)
    # 同じインスタンスをもう一度 save() しても印が消えないよう、メモリ上の値も揃える
    for field_name, value in marked.items():
        setattr(instance, field_name, value)


def _was_auto_generation_target(old: dict) -> bool:
    """保存前に自動生成の対象だったか。新規・論理削除からの復元（旧値が無い）は対象外だったとみなす。"""
    if not old:
        return False
    return EventDetail.is_auto_generation_target(
        detail_type=old.get('detail_type'),
        article_consent=old.get('article_consent'),
        status=old.get('status'),
        deleted_at=None,  # 旧値は論理削除されていない行からだけ読む
        has_slide=bool(old.get('slide_file')),
        youtube_url=old.get('youtube_url'),
    )


def _article_inputs_changed(instance: EventDetail, old: dict) -> bool:
    """YouTube 動画・PDF が新しく入った・変わったか。外しただけの時は含めない。

    動画は URL ではなく動画 ID で比べる（再生位置 ?t= の付け替えや Discord のリンクでは作り直さない）。
    """
    slide_name = instance.slide_file.name if instance.slide_file else ''
    if slide_name and slide_name != (old.get('slide_file') or ''):
        return True
    video_id = instance.video_id
    return bool(video_id) and video_id != youtube_video_id(old.get('youtube_url'))


def _article_emptied(instance: EventDetail, old: dict, update_fields=None) -> bool:
    """この保存で記事のタイトルと本文を両方空にしたか。

    空にした記事は、自動生成した記事でも手で書いた記事でも未作成と同じに扱い、作り直す
    （``article_state`` の判定と揃える）。空のまま保存し直しただけでは印を付けない
    （諦めた生成を保存のたびに数え直さないため）。
    書かなかった列は保存前の値で見る（読み込んだ後に作られた記事を、書いていない古い値で
    空にしたと誤って判定しないため。フル保存も変えていない記事の列は書かない）。
    """
    if article_is_empty(old.get('h1'), old.get('contents')):
        return False
    saved = {
        name: getattr(instance, name) if update_fields is None or name in update_fields else old.get(name)
        for name in ('h1', 'contents')
    }
    return article_is_empty(saved['h1'], saved['contents'])


def _related_list_changed(instance: EventDetail, old: dict, created: bool) -> bool:
    """関連一覧に出る内容が変わりうる保存か（h1・記事化の同意・承認状態・論理削除と復元）。

    旧値は論理削除されていない行からだけ読むので、既存の行で旧値が無いのは論理削除からの復元。
    """
    if created:
        return bool(instance.h1)
    if not old:
        return True
    return (
        (old.get('h1') or '') != (instance.h1 or '')
        or old.get('article_consent') != instance.article_consent
        or old.get('status') != instance.status
        or instance.deleted_at is not None
    )


def _clear_related_cache_if_changed(instance: EventDetail, old: dict, created: bool) -> None:
    """関連一覧に出る内容が変わったら、その集会の関連一覧のキャッシュを消す。

    NG に変えた発表の h1 や、却下・論理削除した発表を、キャッシュの寿命（1 時間）の間
    ほかの発表のページに出し続けないため。
    消すのはコミットの後（ta_hub のトップページのキャッシュと同じ）。コミット前に消すと、その間に
    別のリクエストが古い内容でキャッシュを作り直すことがある。
    """
    if not _related_list_changed(instance, old, created):
        return
    community_ids = {old.get('event__community_id')}
    if old.get('event_id') != instance.event_id:
        community_ids.add(
            Event.objects.filter(pk=instance.event_id).values_list('community_id', flat=True).first()
        )
    keys = [related_event_details_cache_key(community_id) for community_id in community_ids if community_id]
    if keys:
        transaction.on_commit(lambda: cache.delete_many(keys))
