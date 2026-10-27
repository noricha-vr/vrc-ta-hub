import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.templatetags.static import static
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import VketCollaboration

logger = logging.getLogger(__name__)
TOKYO = ZoneInfo('Asia/Tokyo')


def _banner_datetime(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = parse_datetime(value)
    except ValueError:
        return None
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, TOKYO)
    return parsed


def get_active_top_banner(now=None):
    """キャッシュせず、告知期間中の公開コラボを開催日順で一つ返す。"""
    now = now if now is not None else timezone.now()
    collaborations = VketCollaboration.objects.exclude(
        phase=VketCollaboration.Phase.DRAFT,
    ).order_by('period_start', 'pk')
    for collaboration in collaborations:
        settings = collaboration.settings_json
        banner = settings.get('top_banner') if isinstance(settings, dict) else None
        if not isinstance(banner, dict):
            continue
        start = _banner_datetime(banner.get('start'))
        if start is None:
            logger.warning('Vket top banner invalid start: collaboration_id=%s', collaboration.pk)
            continue
        if banner.get('end'):
            end = _banner_datetime(banner['end'])
        else:
            end = datetime.combine(collaboration.period_end + timedelta(days=1), time.min, TOKYO)
        if end is None or end <= start:
            logger.warning('Vket top banner invalid end: collaboration_id=%s', collaboration.pk)
            continue
        if not start <= now < end:
            continue

        theme = banner.get('theme')
        if theme not in ('winter', 'summer'):
            theme = 'winter' if collaboration.period_start.month in {11, 12, 1, 2, 3} else 'summer'
        image = banner.get('image')
        if isinstance(image, str) and image:
            image = image if image.startswith(('http://', 'https://')) else static(image)
        else:
            image = None
        link = banner.get('link')
        if not isinstance(link, str) or not link.startswith(('http://', 'https://')):
            link = None
        start_date, end_date = collaboration.period_start, collaboration.period_end
        return {
            'collaboration': collaboration,
            'heading': f'{start_date.month}月{start_date.day}日〜{end_date.month}月{end_date.day}日は{collaboration.name}！',
            'theme': theme,
            'emoji': '❄️' if theme == 'winter' else '🌻',
            'image': image,
            'link': link or collaboration.get_absolute_url(),
            'has_official_link': bool(link),
        }
    return None
