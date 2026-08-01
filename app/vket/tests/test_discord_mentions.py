"""Vketコラボ機能のテスト."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from community.models import Community
from vket.models import (
    VketCollaboration,
    VketParticipation,
)


User = get_user_model()


class BuildDiscordMentionsTests(TestCase):
    """_build_discord_mentionsのユニットテスト."""

    def setUp(self):
        from allauth.socialaccount.models import SocialAccount

        self.user1 = User.objects.create_user(
            user_name='user1', email='u1@example.com', password='pass',
        )
        self.user2 = User.objects.create_user(
            user_name='user2', email='u2@example.com', password='pass',
        )
        self.user3 = User.objects.create_user(
            user_name='user3', email='u3@example.com', password='pass',
        )
        SocialAccount.objects.create(user=self.user1, provider='discord', uid='111')
        SocialAccount.objects.create(user=self.user2, provider='discord', uid='222')
        SocialAccount.objects.create(user=self.user3, provider='discord', uid='333')

        community1 = Community.objects.create(name='集会1', status='approved', frequency='毎週')
        community2 = Community.objects.create(name='集会2', status='approved', frequency='毎週')
        community3 = Community.objects.create(name='集会3', status='approved', frequency='毎週')
        today = timezone.localdate()
        self.collab = VketCollaboration.objects.create(
            slug='mention-test', name='テスト', period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=3),
            lt_deadline=today + timedelta(days=5),
            phase=VketCollaboration.Phase.ENTRY_OPEN,
        )

        self.p1 = VketParticipation.objects.create(
            collaboration=self.collab, community=community1,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
            applied_by=self.user1, progress=VketParticipation.Progress.APPLIED,
        )
        self.p2 = VketParticipation.objects.create(
            collaboration=self.collab, community=community2,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
            applied_by=self.user2, progress=VketParticipation.Progress.STAGE_REGISTERED,
        )
        self.p3 = VketParticipation.objects.create(
            collaboration=self.collab, community=community3,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
            applied_by=self.user3, progress=VketParticipation.Progress.REHEARSAL,
        )

    def _call(self):
        from vket.views import ManageView
        parts = VketParticipation.objects.filter(collaboration=self.collab)
        return ManageView._build_discord_mentions(parts)

    def test_stage_not_registered_contains_applied(self):
        """APPLIED状態の参加者がstage_not_registeredメンションに含まれる."""
        result = self._call()
        self.assertIn('<@111>', result['stage_not_registered'])
        self.assertNotIn('<@222>', result['stage_not_registered'])

    def test_lt_not_registered_contains_stage_registered(self):
        """STAGE_REGISTERED状態の参加者がlt_not_registeredメンションに含まれる."""
        result = self._call()
        self.assertIn('<@222>', result['lt_not_registered'])
        self.assertNotIn('<@111>', result['lt_not_registered'])

    def test_rehearsal_not_in_individual_groups(self):
        """REHEARSAL状態の参加者は個別グループに含まれずallのみに含まれる."""
        result = self._call()
        self.assertIn('<@333>', result['all'])
        self.assertNotIn('<@333>', result['stage_not_registered'])
        self.assertNotIn('<@333>', result['lt_not_registered'])
        self.assertNotIn('<@333>', result['not_applied'])

    def test_all_contains_everyone(self):
        """全参加者メンションに全員が含まれる."""
        result = self._call()
        self.assertIn('<@111>', result['all'])
        self.assertIn('<@222>', result['all'])
        self.assertIn('<@333>', result['all'])
