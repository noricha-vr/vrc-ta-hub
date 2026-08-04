"""is_event_datetime_locked のユニットテスト。

ロック対象はコラボ本体イベント（VketParticipation.published_event）のみで、
同じ集会の通常イベントは期間内でもロックしない（Issue #571）。
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from event.datetime_lock import is_event_datetime_locked
from tests.factories import make_community, make_event, make_user
from vket.models import VketCollaboration, VketParticipation


class IsEventDatetimeLockedTests(TestCase):
    def setUp(self):
        self.owner = make_user(
            user_name='lock_owner',
            email='lock_owner@example.com',
        )
        self.community = make_community(name='ロック判定集会', owner=self.owner)
        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='datetime-lock-test',
            name='Vket Datetime Lock Test',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today,
            lt_deadline=today + timedelta(days=3),
        )
        self.collab_event = make_event(
            self.community, event_date=today + timedelta(days=1),
        )
        self.regular_event = make_event(
            self.community, event_date=today + timedelta(days=2),
        )

    def _create_participation(self, published_event=None, **kwargs):
        kwargs.setdefault('lifecycle', VketParticipation.Lifecycle.ACTIVE)
        return VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            published_event=published_event,
            **kwargs,
        )

    def test_collab_event_in_period_is_locked(self):
        """コラボ本体イベントは期間内ならロックされる"""
        self._create_participation(published_event=self.collab_event)

        self.assertTrue(is_event_datetime_locked(self.collab_event, self.owner))

    def test_regular_event_of_participating_community_is_not_locked(self):
        """コラボ参加集会でも、コラボ本体でない通常イベントはロックされない"""
        self._create_participation(published_event=self.collab_event)

        self.assertFalse(is_event_datetime_locked(self.regular_event, self.owner))

    def test_participation_without_published_event_is_not_locked(self):
        """published_event 未設定の参加だけならロックされない"""
        self._create_participation(published_event=None)

        self.assertFalse(is_event_datetime_locked(self.collab_event, self.owner))

    def test_declined_participation_is_not_locked(self):
        """不参加の場合はロックされない"""
        self._create_participation(
            published_event=self.collab_event,
            lifecycle=VketParticipation.Lifecycle.DECLINED,
        )

        self.assertFalse(is_event_datetime_locked(self.collab_event, self.owner))

    def test_superuser_is_not_locked(self):
        """superuser はロック対象外"""
        superuser = make_user(
            user_name='lock_admin',
            email='lock_admin@example.com',
            is_staff=True,
            is_superuser=True,
        )
        self._create_participation(published_event=self.collab_event)

        self.assertFalse(is_event_datetime_locked(self.collab_event, superuser))
