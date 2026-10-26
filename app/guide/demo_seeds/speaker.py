"""発表者ガイド用の状態。撮影専用 SQLite の共通データを補う。"""
from event.models import EventDetail


def seed(context):
    community = context['community']
    community.accepts_lt_application = True
    community.recording_allowed = True
    community.default_recording_policy = 'public'
    community.lt_application_template = '【発表概要】\n\n【希望する時間】\n'
    community.default_lt_duration = 30
    community.lt_start_offset_minutes = 15
    community.save(update_fields=[
        'accepts_lt_application', 'recording_allowed', 'default_recording_policy',
        'lt_application_template', 'default_lt_duration', 'lt_start_offset_minutes',
    ])
    # 件数と固定日付を保ち、本人の一覧で承認待ちと承認済みを見比べられるようにする。
    EventDetail.objects.filter(event__community=community).update(
        applicant=context['users']['speaker'], speaker='アオイ', article_consent='ok',
    )
