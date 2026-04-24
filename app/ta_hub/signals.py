from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from event.models import EventDetail
from ta_hub.index_cache import clear_index_view_cache

INDEX_VISIBLE_DETAIL_TYPES = {'LT', 'SPECIAL'}


@receiver(pre_save, sender=EventDetail)
def remember_event_detail_index_type(sender, instance, **kwargs):
    """保存前の種別を保持し、LT/SPECIALから別種別へ変わるケースも拾う。"""
    instance._old_index_detail_type = None
    if not instance.pk:
        return

    try:
        old = EventDetail.objects.only('detail_type').get(pk=instance.pk)
    except EventDetail.DoesNotExist:
        return

    instance._old_index_detail_type = old.detail_type


def _touches_index_visible_detail(instance):
    return (
        instance.detail_type in INDEX_VISIBLE_DETAIL_TYPES
        or getattr(instance, '_old_index_detail_type', None) in INDEX_VISIBLE_DETAIL_TYPES
    )


@receiver(post_save, sender=EventDetail)
def clear_index_cache_on_event_detail_save(sender, instance, **kwargs):
    if not _touches_index_visible_detail(instance):
        return

    transaction.on_commit(clear_index_view_cache)


@receiver(post_delete, sender=EventDetail)
def clear_index_cache_on_event_detail_delete(sender, instance, **kwargs):
    if instance.detail_type not in INDEX_VISIBLE_DETAIL_TYPES:
        return

    transaction.on_commit(clear_index_view_cache)
