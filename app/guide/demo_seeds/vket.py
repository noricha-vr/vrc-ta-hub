"""実在の企画・外部送信を使わないコラボ撮影用データ。"""
from datetime import datetime, time, timedelta, timezone

from django.conf import settings

from community.models import Community

from guide.demo_seeds.browse import demo_image
from vket.models import VketCollaboration, VketNotice, VketNoticeReceipt, VketParticipation, VketPresentation


def seed(context):
    if not getattr(settings, 'GUIDE_CAPTURE_EXTENDED_DEMO', False):
        return
    base = context['base_date']
    collaboration = VketCollaboration.objects.create(
        slug='guide-demo-2030', name='デモコラボ 2030', phase='entry_open',
        period_start=base + timedelta(days=7), period_end=base + timedelta(days=13),
        registration_deadline=base + timedelta(days=5), lt_deadline=base + timedelta(days=6),
        description='ガイド撮影用の架空のコラボです。実際の募集ではありません。',
        settings_json={'stage_registration_open': True, 'stage_url': 'https://example.com/demo-stage',
                       'stage_registration_guidance': {'venue': 'デモ会場', 'tag': 'デモステージ'}},
    )
    stamp = datetime(2030, 4, 1, 3, tzinfo=timezone.utc)
    participation = VketParticipation.objects.create(
        collaboration=collaboration, community=context['community'], progress='applied',
        # 確定日が集会の通常の開催回（月曜）と同じ日時だとコラボ本体とみなされて編集がロックされ、
        # イベント管理の撮影が変わるので、開催回の無い火曜にする。
        requested_date=base + timedelta(days=8), requested_start_time=time(22), requested_duration=60,
        confirmed_date=base + timedelta(days=8), confirmed_start_time=time(22), confirmed_duration=60,
        applied_by=context['users']['owner'], applied_at=stamp,
        stage_thumbnail_generated_at=stamp,
    )
    participation.stage_thumbnail.save('guide-demo-stage.jpg', demo_image('さくら技術交流会\n\n2030/4/9（火）22:00\n\nデモ用の架空の告知', (960, 540)), save=False)
    participation.save(update_fields=['stage_thumbnail'])
    VketPresentation.objects.create(participation=participation, speaker='アオイ',
                                    theme='はじめてのワールド制作', status='confirmed',
                                    requested_start_time=time(22), confirmed_start_time=time(22))
    for title, requires_ack, acknowledged in [
        ('開催準備の確認をお願いします', True, False), ('資料の準備ができました', True, True),
        ('デモ会場のご案内', False, False),
    ]:
        notice = VketNotice.objects.create(
            collaboration=collaboration, title=title, body='これは架空のお知らせです。\n\n開催日と集合場所を確認してください。',
            requires_ack=requires_ack, created_by=context['users']['owner'], sent_at=stamp,
        )
        VketNoticeReceipt.objects.create(
            notice=notice, participation=participation,
            acknowledged_at=stamp if acknowledged else None,
            acknowledged_by=context['users']['owner'] if acknowledged else None,
            ack_token=f'00000000-0000-4000-8000-{notice.pk:012d}',
        )

    # 未生成のサムネイル欄も、外部の画像生成を呼ばずに撮影する。
    VketParticipation.objects.create(
        collaboration=collaboration, community=Community.objects.get(name='こはるの学びの会'),
        progress='applied', requested_date=base + timedelta(days=9),
        requested_start_time=time(21), requested_duration=60,
    )
