"""整理した旧発表URLを、公開中の正本へ転送する（既存JSON設定を利用）。"""
from django.http import HttpResponseRedirect, HttpResponsePermanentRedirect
from django.urls import reverse

from event.models import EventDetail
from vket.models import VketCollaboration

REDIRECTS_KEY = 'event_detail_redirects'


def detail_redirect(pk):
    key = f'detail-{pk}'
    # 設定全体やWebhookを取得せず、対象URLの転送設定だけを読む。
    values = VketCollaboration.objects.filter(
        **{f'settings_json__{REDIRECTS_KEY}__has_key': key},
    ).values_list(f'settings_json__{REDIRECTS_KEY}__{key}', flat=True)
    targets = []
    for value in values:
        if not isinstance(value, dict):
            continue
        target = value.get('target')
        if isinstance(target, int) and not isinstance(target, bool) and target > 0 and target != pk:
            targets.append((target, value.get('permanent') is True))
    if len(set(target for target, _ in targets)) != 1:
        return None
    target = targets[0][0]
    if not EventDetail.objects.filter(pk=target, status='approved').exists():
        return None
    response = HttpResponsePermanentRedirect if all(permanent for _, permanent in targets) else HttpResponseRedirect
    return response(reverse('event:detail', kwargs={'pk': target}))
