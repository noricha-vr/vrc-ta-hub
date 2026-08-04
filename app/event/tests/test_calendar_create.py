"""カレンダーイベント登録の重複ハンドリングのテスト.

Issue #568: 重複作成の競合で IntegrityError が exc_info 付き error ログになり、
Error Reporting に incident として蓄積されていた。重複判定を unique 制約の
IntegrityError 捕捉に一本化し、フォームエラーとして扱うことを検証する。
"""
from datetime import time, timedelta
from unittest.mock import patch

from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from event.models import Event
from tests.factories import make_community, make_event, make_user


class CalendarCreateDuplicateTest(TestCase):
    def setUp(self):
        self.user = make_user(user_name='calendar_dup_owner')
        self.community = make_community(
            name='重複登録テスト集会',
            owner=self.user,
            start_time=time(21, 0),
        )
        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['active_community_id'] = self.community.pk
        session.save()

    def test_duplicate_datetime_shows_form_error_instead_of_500(self):
        """同一日時のイベントが既にある場合、500 ではなくフォームエラーになる."""
        event_date = timezone.localdate() + timedelta(days=10)
        make_event(
            self.community,
            event_date=event_date,
            start_time=time(21, 0),
        )

        response = self.client.post(
            reverse('event:calendar_create'),
            {
                'start_date': event_date.isoformat(),
                'start_time': '21:00',
                'duration': 60,
                'recurrence_type': 'none',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'すでにイベントが登録されています')
        self.assertEqual(
            Event.objects.filter(community=self.community, date=event_date).count(),
            1,
        )

    def test_duplicate_datetime_does_not_emit_error_log(self):
        """重複はエラーログ（Error Reporting の incident 化対象）にしない."""
        event_date = timezone.localdate() + timedelta(days=10)
        make_event(
            self.community,
            event_date=event_date,
            start_time=time(21, 0),
        )

        with self.assertLogs('event.views.calendar_create', level='WARNING') as logs:
            self.client.post(
                reverse('event:calendar_create'),
                {
                    'start_date': event_date.isoformat(),
                    'start_time': '21:00',
                    'duration': 60,
                    'recurrence_type': 'none',
                },
            )

        self.assertFalse(
            [r for r in logs.records if r.levelname == 'ERROR'],
            'IntegrityError が error ログとして記録されている',
        )

    def test_race_condition_integrity_error_is_handled_as_duplicate(self):
        """CREATE時にはじめて衝突が発覚する競合でも、500やerrorログにしない.

        Issue #568 の本質は「事前チェックをすり抜けた並行作成の衝突」。
        単一スレッドのテストでは真の競合を再現できないため、プロセス外の
        並行性の代理として create の IntegrityError を mock で注入する
        （内部 patch はモック境界違反だが、並行性の再現は他手段が無いため許容）。
        """
        event_date = timezone.localdate() + timedelta(days=10)

        with patch.object(
            Event.objects,
            'create',
            side_effect=IntegrityError(
                "Duplicate entry '1-2026-08-14-21:00:00.000000' "
                "for key 'event.event_unique_community_date_start_time'"
            ),
        ):
            with self.assertLogs('event.views.calendar_create', level='WARNING') as logs:
                response = self.client.post(
                    reverse('event:calendar_create'),
                    {
                        'start_date': event_date.isoformat(),
                        'start_time': '21:00',
                        'duration': 60,
                        'recurrence_type': 'none',
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'すでにイベントが登録されています')
        self.assertFalse(
            [r for r in logs.records if r.levelname == 'ERROR'],
            '競合時の IntegrityError が error ログとして記録されている',
        )
