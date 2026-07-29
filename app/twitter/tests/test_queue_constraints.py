"""投稿キュー制約のテスト。"""

import datetime
from django.db import IntegrityError

from django.test import TestCase, tag
from django.utils import timezone

from community.models import Community
from event.models import Event
from twitter.models import TweetQueue

@tag('offline_external_api')
class TweetQueueConstraintTest(TestCase):
    """TweetQueue の一意制約テスト"""

    def test_daily_reminder_unique_per_event(self):
        """daily_reminder は同一イベントに1件しか作れない"""
        community = Community.objects.create(
            name="Constraint Community",
            start_time=datetime.time(21, 0),
            duration=60,
            weekdays=["Mon"],
            frequency="毎週",
            organizers="Test",
            description="制約テスト用",
            platform="All",
            status="approved",
        )
        event = Event.objects.create(
            community=community,
            date=timezone.localdate(),
            start_time=datetime.time(21, 0),
            duration=60,
        )

        TweetQueue.objects.create(
            tweet_type="daily_reminder",
            community=community,
            event=event,
        )

        with self.assertRaises(IntegrityError):
            TweetQueue.objects.create(
                tweet_type="daily_reminder",
                community=community,
                event=event,
            )
