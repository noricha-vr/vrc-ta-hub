"""参加ごとのサムネイル生成と、条件付き更新による二重実行の防止。"""

import base64
from io import BytesIO
import os
from time import monotonic
import uuid

from django.core.files.base import ContentFile
from django.db.models import F
from django.utils import timezone
from PIL import Image
import requests
import structlog

from website.constants import OPENROUTER_BASE_URL

from .models import VketParticipation
from .stage_copy import thumbnail_prompt

logger = structlog.get_logger(__name__)
THUMBNAIL_TIMEOUT = 110
# 失敗でも課金され得るため、成功・失敗を問わず API を呼べる回数を絞る。
MAX_THUMBNAIL_ATTEMPTS = 3


def log_thumbnail(participation, result, elapsed=0, **extra):
    logger.info(
        'vket_stage_thumbnail', community_id=participation.community_id,
        participation_id=participation.pk, duration_seconds=round(elapsed, 3), result=result, **extra,
    )


def _generate_jpeg(participation) -> bytes:
    """ポスターを添付して生成し、応答画像をJPEGに変換する。"""
    key = os.environ.get('OPENROUTER_API_KEY', '')
    if not key:
        raise ValueError('画像生成を利用できません。')
    with participation.community.poster_image.open('rb') as poster:
        with Image.open(poster) as image:
            image.thumbnail((2048, 2048))
            buffer = BytesIO()
            image.convert('RGB').save(buffer, format='JPEG', quality=90)
    data_url = 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
    response = requests.post(
        f'{OPENROUTER_BASE_URL}/chat/completions',
        headers={'Authorization': f'Bearer {key}'},
        json={
            'model': 'openai/gpt-5.4-image-2', 'modalities': ['image', 'text'],
            'image_config': {'aspect_ratio': '16:9'},
            'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': thumbnail_prompt(participation)},
                {'type': 'image_url', 'image_url': {'url': data_url}},
            ]}],
        },
        timeout=THUMBNAIL_TIMEOUT,
    )
    response.raise_for_status()
    url = response.json()['choices'][0]['message']['images'][0]['image_url']['url']
    header, encoded = url.split(',', 1)
    if header not in {'data:image/png;base64', 'data:image/jpeg;base64', 'data:image/webp;base64'} or len(encoded) > 28_000_000:
        raise ValueError('画像の応答形式が不正です。')
    with Image.open(BytesIO(base64.b64decode(encoded, validate=True))) as image:
        if image.width * 9 != image.height * 16 or image.width * image.height > 16_000_000:
            raise ValueError('画像の縦横比またはサイズが不正です。')
        output = BytesIO()
        image.convert('RGB').save(output, format='JPEG', quality=95)
        return output.getvalue()


def generate_stage_thumbnail(participation) -> str:
    """予約に成功したリクエストだけ生成する。失敗時は自分の予約だけを解除する。"""
    if not participation.community.poster_image:
        return 'no_poster'
    started = monotonic()
    requested_at = timezone.now()
    rows = VketParticipation.objects.filter(
        pk=participation.pk, stage_thumbnail_requested_at__isnull=True,
        stage_thumbnail_generated_at__isnull=True, stage_thumbnail='',
        stage_thumbnail_attempts__lt=MAX_THUMBNAIL_ATTEMPTS,
    ).update(stage_thumbnail_requested_at=requested_at, stage_thumbnail_attempts=F('stage_thumbnail_attempts') + 1)
    if not rows:
        if VketParticipation.objects.filter(
            pk=participation.pk, stage_thumbnail_generated_at__isnull=True,
            stage_thumbnail_requested_at__isnull=True, stage_thumbnail_attempts__gte=MAX_THUMBNAIL_ATTEMPTS,
        ).exists():
            log_thumbnail(participation, 'attempts_exhausted')
            return 'attempts_exhausted'
        return 'already_requested'

    reservation = VketParticipation.objects.filter(
        pk=participation.pk, stage_thumbnail_requested_at=requested_at,
        stage_thumbnail_generated_at__isnull=True,
    )
    try:
        data = _generate_jpeg(participation)
        participation.stage_thumbnail.save(f'{participation.pk}-{uuid.uuid4().hex}.jpg', ContentFile(data), save=False)
        stored = reservation.update(
            stage_thumbnail=participation.stage_thumbnail.name, stage_thumbnail_generated_at=timezone.now(),
        )
        if not stored:
            participation.stage_thumbnail.delete(save=False)
            log_thumbnail(participation, 'reservation_changed', monotonic() - started)
            return 'already_requested'
    except Exception as exc:
        # 応答や例外の本文には認証情報が含まれ得るため、ログには例外の種類だけを残す。
        reservation.update(stage_thumbnail_requested_at=None)
        log_thumbnail(participation, 'failed', monotonic() - started, error_type=type(exc).__name__)
        return 'failed'
    log_thumbnail(participation, 'generated', monotonic() - started)
    return 'generated'
