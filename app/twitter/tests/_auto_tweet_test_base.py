"""自動投稿テストで共有するセットアップを提供する。"""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.utils import timezone

from community.models import Community, CommunityMember
from event.models import Event

CustomUser = get_user_model()


@tag('offline_external_api')
class AutoTweetTestBase(TestCase):
    """テスト共通のセットアップ"""

    def setUp(self):
        self.client = Client()
        self.owner = CustomUser.objects.create_user(
            user_name="auto_tweet_owner",
            email="auto_tweet_owner@example.com",
            password="testpassword",
        )
        # status=pending で作成 (承認前)
        self.community = Community.objects.create(
            name="Auto Tweet Community",
            start_time=datetime.time(22, 0),
            duration=60,
            weekdays=["Mon", "Thu"],
            frequency="毎週",
            organizers="Test Organizer",
            description="テスト用の技術系集会です",
            platform="All",
            status="pending",
            twitter_hashtag="TestMeetup",
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER,
        )
        self.event = Event.objects.create(
            community=self.community,
            date=datetime.date(2099, 5, 1),
            start_time=datetime.time(22, 0),
            duration=60,
        )

    def due_scheduled_at(self):
        return timezone.now() - datetime.timedelta(minutes=5)

    def future_scheduled_at(self):
        return timezone.now() + datetime.timedelta(hours=1)

    def overdue_scheduled_at(self):
        return timezone.now() - datetime.timedelta(hours=25)


@tag('offline_external_api')
class TweetGeneratorTestBase(TestCase):
    """告知文生成テストで共有するセットアップを提供する。"""

    def setUp(self):
        with patch("twitter.services.tweet_generation.threading.Thread"):
            self.community = Community.objects.create(
                name="Generator Test Community",
                start_time=datetime.time(22, 0),
                duration=60,
                weekdays=["Mon"],
                frequency="毎週",
                organizers="Test",
                description="テスト用集会",
                platform="All",
                status="approved",
                twitter_hashtag="GenTest",
            )
        self.event = Event.objects.create(
            community=self.community,
            date=datetime.date(2026, 5, 1),
            start_time=datetime.time(22, 0),
            duration=60,
        )
