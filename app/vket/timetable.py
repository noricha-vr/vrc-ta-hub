from datetime import timedelta

from django.db.models import Prefetch

from event.models import EventDetail
from event_calendar.calendar_utils import build_google_calendar_url
from utils.vrchat_time import get_vrchat_today

# 本番の縦の縮尺（1 分 = 2px）。LT の見出しを枠内のどの高さに置くかの計算に使う
PX_PER_MINUTE = 2
# 枠の上端にある集会名と時刻の2行の高さ。LT の見出しはこれより下に置く
HEADER_PX = 34
LT_LABEL_PX = 30
# 時刻の列の幅
TIME_AXIS_PX = 64


def _minutes(value):
    return value.hour * 60 + value.minute + value.second / 60


def _lt_segments(event, details, visible_minutes):
    """集会の開催時間のうち LT がある区間を、枠の高さに対する割合と見出しの位置にする。

    visible_minutes は表に描く枠の長さ（日付をまたぐ集会は 24:00 で切れる）。
    0 時を過ぎてから始まる LT はレールに描けないので、見出しだけ出す。
    """
    event_start = _minutes(event.start_time)
    body_px = visible_minutes * PX_PER_MINUTE - 6
    segments = []
    floor = HEADER_PX
    for detail in details:
        offset = _minutes(detail.start_time) - event_start
        if offset < 0 and offset + 24 * 60 < event.duration:
            # 日付をまたぐ集会の、0 時以降に始まる LT
            offset += 24 * 60
        start = max(offset, 0)
        end = min(offset + (detail.duration or 0), event.duration)
        if end <= start:
            continue
        visible_end = min(end, visible_minutes)
        anchor = start * PX_PER_MINUTE - 3 if start < visible_minutes else floor
        label_top = round(max(anchor, floor))
        show_label = label_top + LT_LABEL_PX - 2 <= body_px
        if show_label:
            floor = label_top + LT_LABEL_PX
        end_minutes = int(event_start + end) % (24 * 60)
        segments.append({
            'time_label': f'{detail.start_time:%H:%M}〜{end_minutes // 60:02d}:{end_minutes % 60:02d}',
            'title': detail.theme or f'発表者: {detail.speaker}',
            'rail_style': (
                f'top:{start / visible_minutes * 100:.4f}%;height:{(visible_end - start) / visible_minutes * 100:.4f}%;'
                if visible_end > start else None
            ),
            'label_style': f'top:{label_top}px;' if show_label else None,
        })
    return segments


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


def build_collaboration_timetable(collaboration, today=None, request=None):
    """公開枠を月曜始まりの週と時間グリッドにする（参加件数によらず2クエリ）。

    request があれば、各枠に Google カレンダーへ追加する URL を付ける。
    """
    today = today if today is not None else get_vrchat_today()
    participations = (
        # トップの他の一覧と同じく、承認済みの集会だけを出す
        collaboration.participations.filter(published_event__isnull=False, community__status='approved')
        .select_related('community', 'published_event', 'published_event__community')
        .prefetch_related(Prefetch(
            'published_event__details',
            queryset=EventDetail.objects.filter(detail_type='LT', status='approved')
            .only('event_id', 'speaker', 'theme', 'start_time', 'duration').order_by('start_time', 'pk'),
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
            'lt_segments': _lt_segments(event, details, end - start),
            'google_calendar_url': build_google_calendar_url(
                request, event, details=[(d.speaker, d.theme) for d in details],
            ) if request is not None else None,
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
        # 同じ時刻に並ぶ集会がある日は、レーンの数だけ列を広げて集会名と時刻を読めるようにする
        lane_counts = [
            max((b['lane_count'] for b in blocks_by_date.get(monday + timedelta(days=i), [])), default=1)
            for i in range(7)
        ]
        weeks.append({
            'number': len(weeks) + 1,
            'label': f'第{len(weeks) + 1}週 {first.month}/{first.day}〜{last.month}/{last.day}',
            'active': monday <= selected_day <= sunday,
            # レーンの最小幅は CSS 変数（タッチ端末ではボタンが大きいので広げる）
            'grid_style': (
                'grid-template-columns:{}px {};min-width:calc({}px + var(--vket-lane-min) * {});'.format(
                    TIME_AXIS_PX,
                    ' '.join(f'minmax(calc(var(--vket-lane-min) * {n}),{n}fr)' for n in lane_counts),
                    TIME_AXIS_PX, sum(lane_counts),
                )
            ),
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
