"""発表申請フォームの開催日候補に関するテスト。"""

from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import Client, TestCase
from django.urls import reverse

from tests.factories import make_community, make_event
from user_account.tests.utils import create_discord_linked_user


class LTApplicationDateChoiceTest(TestCase):
    """発表申請フォームの開催日候補を検証する。"""

    def setUp(self):
        self.client = Client()
        self.user = create_discord_linked_user(
            user_name='date_choice_user',
            email='date-choice@example.com',
            password='testpass123',
        )
        self.community = make_community(owner=self.user)

    def test_starts_with_nearest_date_and_excludes_jst_yesterday(self):
        """JSTの当日を初期選択し、前日の開催回は候補から除外する。"""
        jst_early_morning = datetime(
            2026, 8, 20, 1, 30, tzinfo=ZoneInfo('Asia/Tokyo')
        )
        yesterday_event = make_event(
            self.community,
            event_date=date(2026, 8, 19),
        )
        today_event = make_event(
            self.community,
            event_date=date(2026, 8, 20),
        )
        make_event(self.community, event_date=date(2026, 8, 21))
        self.client.force_login(self.user)
        url = reverse(
            'event:lt_application_create',
            kwargs={'community_pk': self.community.pk},
        )

        with patch(
            'django.utils.timezone.now',
            return_value=jst_early_morning.astimezone(ZoneInfo('UTC')),
        ):
            form = self.client.get(url).context['form']

        self.assertNotIn(yesterday_event, form.fields['event'].queryset)
        self.assertIn(today_event, form.fields['event'].queryset)
        self.assertEqual(form.fields['event'].initial, today_event.pk)
