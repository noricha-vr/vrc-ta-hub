"""community.forms_processor の単体テスト

承認/非承認/閉鎖の副作用（Email / Discord / cleanup）が silent failure で
吸われていた箇所を中心にカバーする。
"""
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, RequestFactory, override_settings
from django.utils import timezone

from community.forms_processor import (
    approve_community_registration,
    cleanup_closed_community,
    close_community_and_cleanup,
    create_owner_membership,
    notify_new_community_registration,
    reject_community_registration,
)
from community.models import CommunityMember
from tests.factories import (
    make_community as _make_community_factory,
    make_user,
)

User = get_user_model()


def _make_user(name="u1", email="u1@example.com"):
    """既存 setUp の呼び出し名互換 wrapper。新規テストは make_user を直接使う。"""
    return make_user(user_name=name, email=email)


def _make_community(owner=None, name="Test Community"):
    """既存 setUp の呼び出し名互換 wrapper。新規テストは make_community を直接使う。

    forms_processor のテストは承認前提なので status="pending" デフォルトを維持する。
    """
    return _make_community_factory(name=name, owner=owner, status="pending")


@override_settings(DEFAULT_FROM_EMAIL="noreply@example.com")
class ApproveCommunityRegistrationTest(TestCase):
    """approve_community_registration の承認 + メール送信"""

    def setUp(self):
        self.factory = RequestFactory()
        self.owner = _make_user("owner1", "owner1@example.com")
        self.community = _make_community(owner=self.owner)
        self.request = self.factory.get("/")

    def test_approves_community_and_sends_email(self):
        """status が approved になり、オーナーへメール送信される"""
        approve_community_registration(self.community, self.request)
        self.community.refresh_from_db()
        self.assertEqual(self.community.status, "approved")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["owner1@example.com"])
        self.assertIn(self.community.name, mail.outbox[0].subject)

    def test_skips_email_when_owner_email_missing(self):
        """オーナーが email 未設定でもクラッシュせず status は approved になる"""
        self.owner.email = ""
        self.owner.save()
        approve_community_registration(self.community, self.request)
        self.community.refresh_from_db()
        self.assertEqual(self.community.status, "approved")
        self.assertEqual(len(mail.outbox), 0)

    def test_skips_email_when_no_owner(self):
        """オーナー不在でもクラッシュせず status は approved"""
        community_no_owner = _make_community(owner=None, name="NoOwnerCommunity")
        approve_community_registration(community_no_owner, self.request)
        community_no_owner.refresh_from_db()
        self.assertEqual(community_no_owner.status, "approved")
        self.assertEqual(len(mail.outbox), 0)


@override_settings(DEFAULT_FROM_EMAIL="noreply@example.com")
class RejectCommunityRegistrationTest(TestCase):
    """reject_community_registration の非承認 + メール送信"""

    def setUp(self):
        self.owner = _make_user("owner1", "owner1@example.com")
        self.community = _make_community(owner=self.owner)

    def test_rejects_community_and_sends_email(self):
        """status が rejected になり、オーナーへメール送信される"""
        reject_community_registration(self.community)
        self.community.refresh_from_db()
        self.assertEqual(self.community.status, "rejected")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["owner1@example.com"])
        self.assertIn("非承認", mail.outbox[0].subject)

    def test_skips_email_when_owner_email_missing(self):
        """オーナーが email 未設定でも status は rejected"""
        self.owner.email = ""
        self.owner.save()
        reject_community_registration(self.community)
        self.community.refresh_from_db()
        self.assertEqual(self.community.status, "rejected")
        self.assertEqual(len(mail.outbox), 0)


class NotifyNewCommunityRegistrationTest(TestCase):
    """notify_new_community_registration の Discord Webhook 送信"""

    def setUp(self):
        self.factory = RequestFactory()
        self.owner = _make_user("owner1", "owner1@example.com")
        self.community = _make_community(owner=self.owner)
        self.request = self.factory.get("/")

    @override_settings(DISCORD_WEBHOOK_URL="")
    def test_skipped_when_webhook_url_empty(self):
        """DISCORD_WEBHOOK_URL 空で何もしない"""
        with patch("community.forms_processor.requests.post") as mock_post:
            notify_new_community_registration(self.community, self.request)
            mock_post.assert_not_called()

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/123/abc")
    @patch("community.forms_processor.requests.post")
    def test_posts_to_webhook_when_url_set(self, mock_post):
        """DISCORD_WEBHOOK_URL 設定時に POST される"""
        mock_post.return_value = MagicMock(ok=True, status_code=200)
        notify_new_community_registration(self.community, self.request)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://discord.com/api/webhooks/123/abc")
        self.assertIn("content", kwargs["json"])
        self.assertIn(self.community.name, kwargs["json"]["content"])

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/123/abc")
    @patch("community.forms_processor.requests.post")
    def test_swallows_request_exception(self, mock_post):
        """requests 例外時もクラッシュしない (silent failure)"""
        mock_post.side_effect = requests.RequestException("network down")
        # 例外を投げずに完了する
        notify_new_community_registration(self.community, self.request)
        mock_post.assert_called_once()


class CloseCommunityAndCleanupTest(TestCase):
    """close_community_and_cleanup の end_at 設定と cleanup 呼び出し"""

    def setUp(self):
        self.owner = _make_user("owner1", "owner1@example.com")
        self.community = _make_community(owner=self.owner)
        self.community.status = "approved"
        self.community.save()

    def test_sets_end_at_to_today_and_calls_cleanup(self):
        """end_at が today に設定され、cleanup_func が翌日以降を対象に呼ばれる"""
        today = timezone.now().date()
        mock_cleanup = MagicMock(return_value={
            "db_events": 3,
            "rules": 1,
            "google_events": 2,
        })
        stats = close_community_and_cleanup(self.community, cleanup_func=mock_cleanup)
        self.community.refresh_from_db()

        self.assertEqual(self.community.end_at, today)
        mock_cleanup.assert_called_once()
        call_kwargs = mock_cleanup.call_args.kwargs
        self.assertEqual(call_kwargs["community"], self.community)
        self.assertEqual(call_kwargs["from_date"], today + timedelta(days=1))
        self.assertTrue(call_kwargs["delete_rules"])
        self.assertTrue(call_kwargs["delete_google_events"])
        self.assertEqual(stats, {"db_events": 3, "rules": 1, "google_events": 2})

    def test_returns_cleanup_stats_directly(self):
        """cleanup_func の返り値がそのまま返る (stats 検証用)"""
        mock_cleanup = MagicMock(return_value={
            "db_events": 0,
            "rules": 0,
            "google_events": 0,
        })
        stats = close_community_and_cleanup(self.community, cleanup_func=mock_cleanup)
        self.assertEqual(stats["db_events"], 0)


class CleanupClosedCommunityTest(TestCase):
    """cleanup_closed_community の end_at 自動設定と cleanup 呼び出し"""

    def setUp(self):
        self.owner = _make_user("owner1", "owner1@example.com")

    def test_sets_end_at_when_none(self):
        """end_at が None なら today に設定される"""
        community = _make_community(owner=self.owner, name="C1")
        community.status = "approved"
        community.end_at = None
        community.save()
        today = timezone.now().date()

        mock_cleanup = MagicMock(return_value={"db_events": 0, "rules": 0, "google_events": 0})
        cleanup_closed_community(community, cleanup_func=mock_cleanup)
        community.refresh_from_db()
        self.assertEqual(community.end_at, today)
        mock_cleanup.assert_called_once()

    def test_preserves_existing_end_at(self):
        """end_at が既に設定済みなら上書きしない"""
        community = _make_community(owner=self.owner, name="C2")
        community.status = "approved"
        past_date = date.today() - timedelta(days=10)
        community.end_at = past_date
        community.save()

        mock_cleanup = MagicMock(return_value={"db_events": 0, "rules": 0, "google_events": 0})
        cleanup_closed_community(community, cleanup_func=mock_cleanup)
        community.refresh_from_db()
        self.assertEqual(community.end_at, past_date)

    def test_passes_tomorrow_as_from_date_to_cleanup(self):
        """cleanup_func には from_date=today+1day が渡される"""
        community = _make_community(owner=self.owner, name="C3")
        community.status = "approved"
        community.save()

        mock_cleanup = MagicMock(return_value={"db_events": 0, "rules": 0, "google_events": 0})
        cleanup_closed_community(community, cleanup_func=mock_cleanup)
        today = timezone.now().date()
        self.assertEqual(mock_cleanup.call_args.kwargs["from_date"], today + timedelta(days=1))


class CreateOwnerMembershipTest(TestCase):
    """create_owner_membership の Owner ロール付与"""

    def test_creates_owner_membership(self):
        """OWNER ロールの CommunityMember が作成される"""
        user = _make_user("u1")
        community = _make_community(owner=None, name="X")
        member = create_owner_membership(community, user)
        self.assertEqual(member.role, CommunityMember.Role.OWNER)
        self.assertEqual(member.user, user)
        self.assertEqual(member.community, community)
