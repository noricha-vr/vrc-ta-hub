"""community.services.activate_community の単体テスト。

GET（event:my_list の ?community=）と POST（community:switch）で二重化していた
受理条件を 1 箇所に集約したもの。受理条件そのものはここで担保する。
"""

import importlib
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from community.models import Community, CommunityMember
from community.services import (
    SESSION_KEY,
    ActivationError,
    activate_community,
)

CustomUser = get_user_model()


def make_session():
    """本物のセッションストアを作る（modified の検証に dict では不十分なため）。"""
    engine = importlib.import_module(settings.SESSION_ENGINE)
    return engine.SessionStore()


class ActivateCommunityTest(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email='svc_user@example.com',
            password='testpass123',
            user_name='サービステストユーザー',
        )
        self.community = Community.objects.create(
            name='所属集会', status='approved', frequency='毎週',
        )
        self.ended_community = Community.objects.create(
            name='終了済み集会', status='approved', frequency='毎週',
            end_at=timezone.now().date() - timedelta(days=1),
        )
        self.foreign_community = Community.objects.create(
            name='非所属集会', status='approved', frequency='毎月',
        )
        for community in (self.community, self.ended_community):
            CommunityMember.objects.create(
                community=community, user=self.user, role=CommunityMember.Role.OWNER,
            )
        self.session = make_session()

    def test_accepts_member_community(self):
        """所属していて終了していない集会は受理され、セッションが更新される"""
        result = activate_community(self.session, self.user, str(self.community.id))

        self.assertTrue(result.is_accepted)
        self.assertIsNone(result.error)
        self.assertEqual(result.community, self.community)
        self.assertTrue(result.session_updated)
        self.assertEqual(self.session[SESSION_KEY], self.community.id)

    def test_accepts_integer_community_id(self):
        """int で渡しても受理される（POST/GET の生値が文字列でない場合）"""
        result = activate_community(self.session, self.user, self.community.id)

        self.assertTrue(result.is_accepted)
        self.assertEqual(self.session[SESSION_KEY], self.community.id)

    def test_rejects_missing_community_id(self):
        """未指定は NOT_SPECIFIED で拒否され、セッションは書き換わらない"""
        for raw in ('', None):
            with self.subTest(raw=raw):
                result = activate_community(self.session, self.user, raw)

                self.assertFalse(result.is_accepted)
                self.assertEqual(result.error, ActivationError.NOT_SPECIFIED)
                self.assertNotIn(SESSION_KEY, self.session)

    def test_rejects_non_numeric_community_id(self):
        """非数値の ID は INVALID_ID で拒否される"""
        result = activate_community(self.session, self.user, 'abc')

        self.assertFalse(result.is_accepted)
        self.assertEqual(result.error, ActivationError.INVALID_ID)
        self.assertIsNone(result.community)
        self.assertNotIn(SESSION_KEY, self.session)

    def test_rejects_non_member_community(self):
        """非所属の集会は NOT_A_MEMBER で拒否される"""
        result = activate_community(
            self.session, self.user, str(self.foreign_community.id)
        )

        self.assertFalse(result.is_accepted)
        self.assertEqual(result.error, ActivationError.NOT_A_MEMBER)
        self.assertNotIn(SESSION_KEY, self.session)

    def test_rejects_nonexistent_community(self):
        """存在しない ID も NOT_A_MEMBER 扱い（メンバーシップが引けないため）"""
        result = activate_community(self.session, self.user, '999999')

        self.assertFalse(result.is_accepted)
        self.assertEqual(result.error, ActivationError.NOT_A_MEMBER)

    def test_rejects_ended_community(self):
        """所属していても終了済みの集会は ENDED で拒否される"""
        result = activate_community(
            self.session, self.user, str(self.ended_community.id)
        )

        self.assertFalse(result.is_accepted)
        self.assertEqual(result.error, ActivationError.ENDED)
        self.assertNotIn(SESSION_KEY, self.session)

    def test_rejection_keeps_existing_active_community(self):
        """拒否時は既存のアクティブ集会を書き換えない"""
        self.session[SESSION_KEY] = self.community.id
        self.session.modified = False

        result = activate_community(
            self.session, self.user, str(self.foreign_community.id)
        )

        self.assertFalse(result.is_accepted)
        self.assertEqual(self.session[SESSION_KEY], self.community.id)
        self.assertFalse(self.session.modified)

    def test_same_value_does_not_touch_session(self):
        """同値の再代入では session.modified が立たない（毎リクエストの DB 書き込み回避）"""
        self.session[SESSION_KEY] = self.community.id
        self.session.modified = False

        result = activate_community(self.session, self.user, str(self.community.id))

        self.assertTrue(result.is_accepted)
        self.assertFalse(result.session_updated)
        self.assertFalse(self.session.modified)
        self.assertEqual(self.session[SESSION_KEY], self.community.id)

    def test_different_value_marks_session_modified(self):
        """別の集会へ切り替える時は session.modified が立つ"""
        self.session[SESSION_KEY] = self.foreign_community.id
        self.session.modified = False

        result = activate_community(self.session, self.user, str(self.community.id))

        self.assertTrue(result.session_updated)
        self.assertTrue(self.session.modified)
        self.assertEqual(self.session[SESSION_KEY], self.community.id)
