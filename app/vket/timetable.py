from datetime import timedelta

from django.db.models import Prefetch

from event.models import EventDetail
from utils.vrchat_time import get_vrchat_today


def _position_day_blocks(blocks, start_minute, span):
    """時間がつながって重なる枠ごとに、必要なレーン数をそろえる。"""
    groups = []
    group = []
    lane_ends = []
    for block in sorted(blocks, key=lambda b: (b['start'], b['end'], b['event_id'])):
        if group and block['start'] >= max(lane_ends):
            groups.append((group, len(lane_ends)))
            group, lane_ends = [], []
        lane = next((i for i, end in enumerate(lane_ends) if end <= block['start']), len(lane_ends))
        if lane == len(lane_ends):
            lane_ends.append(block['end'])
        else:
            lane_ends[lane] = block['end']
        block['lane'] = lane
        group.append(block)
    if group:
        groups.append((group, len(lane_ends)))
    for group, lanes in groups:
        for block in group:
            block['lane_count'] = lanes
            top = (block['start'] - start_minute) / span * 100
            height = (block['end'] - block['start']) / span * 100
            block['style'] = (
                f'top:{top:.4f}%;height:{height:.4f}%;'
                f'left:{block["lane"] / lanes * 100:.4f}%;width:{100 / lanes:.4f}%;'
            )


def build_collaboration_timetable(collaboration, today=None):
    """公開枠を月曜始まりの週と時間グリッドにする（参加件数によらず2クエリ）。"""
    today = today if today is not None else get_vrchat_today()
    participations = (
        # トップの他の一覧と同じく、承認済みの集会だけを出す
        collaboration.participations.filter(published_event__isnull=False, community__status='approved')
        .select_related('community', 'published_event')
        .prefetch_related(Prefetch(
            'published_event__details',
            queryset=EventDetail.objects.filter(detail_type='LT', status='approved')
            .only('event_id', 'speaker', 'theme', 'start_time').order_by('start_time', 'pk'),
            to_attr='timetable_details',
        ))
        .order_by('published_event__date', 'published_event__start_time', 'pk')
    )
    blocks_by_date = {}
    for participation in participations:
        event = participation.published_event
        if not collaboration.period_start <= event.date <= collaboration.period_end or event.duration <= 0:
            continue
        start = event.start_time.hour * 60 + event.start_time.minute + event.start_time.second / 60
        end = min(start + event.duration, 24 * 60)
        details = [d for d in event.timetable_details if d.speaker or d.theme]
        end_minutes = int((start + event.duration) % (24 * 60))
        block = {
            'event_id': event.pk,
            'community': participation.community,
            'start': start,
            'end': end,
            'time_label': f'{event.start_time:%H:%M}〜{end_minutes // 60:02d}:{end_minutes % 60:02d}',
            'has_lt': bool(details),
            'titles': [d.theme or f'発表者: {d.speaker}' for d in details],
        }
        blocks_by_date.setdefault(event.date, []).append(block)

    blocks = [b for day in blocks_by_date.values() for b in day]
    start_minute = int(min(b['start'] for b in blocks) // 60) * 60 if blocks else 0
    end_minute = int(-(-max(b['end'] for b in blocks) // 60)) * 60 if blocks else 0
    span = end_minute - start_minute
    ticks = [
        {'label': f'{minute // 60:02d}:{minute % 60:02d}', 'style': f'top:{(minute - start_minute) / span * 100:.4f}%;'}
        for minute in range(start_minute, end_minute + 1, 30)
    ] if span else []
    for day_blocks in blocks_by_date.values():
        _position_day_blocks(day_blocks, start_minute, span)

    weeks = []
    monday = collaboration.period_start - timedelta(days=collaboration.period_start.weekday())
    selected_day = min(max(today, collaboration.period_start), collaboration.period_end)
    while monday <= collaboration.period_end:
        sunday = monday + timedelta(days=6)
        first = max(monday, collaboration.period_start)
        last = min(sunday, collaboration.period_end)
        weeks.append({
            'number': len(weeks) + 1,
            'label': f'第{len(weeks) + 1}週 {first.month}/{first.day}〜{last.month}/{last.day}',
            'active': monday <= selected_day <= sunday,
            'days': [
                {
                    'date': day,
                    'weekday': '月火水木金土日'[day.weekday()],
                    'is_today': day == today,
                    'in_period': collaboration.period_start <= day <= collaboration.period_end,
                    'blocks': blocks_by_date.get(day, []),
                }
                for day in (monday + timedelta(days=i) for i in range(7))
            ],
        })
        monday += timedelta(days=7)
    return {
        'weeks': weeks,
        'ticks': ticks,
        'height': span * 2,
        'start_minute': start_minute,
        'end_minute': end_minute,
        'scheduled_count': len(blocks),
    }
