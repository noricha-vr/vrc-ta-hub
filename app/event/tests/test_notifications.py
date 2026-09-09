"""event.notifications の単体テスト

メール/Discord Webhook の通知送信パスをカバーする。
silent failure を起こしうる recipient リスト構築・例外ハンドリングを重点的に検証する。
"""
from datetime import datetime
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

import requests
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings

from community.models import CommunityMember
from event.notifications import (
    notify_owners_of_new_application,
    notify_applicant_of_result,
    notify_slide_material_published,
    notify_speaker_account_linked,
    _send_discord_notification_for_new_application,
    _send_discord_notification_for_result,
)
from tests.factories import (
    make_community as _make_community_factory,
    make_event,
    make_event_detail,
    make_user,
)

User = get_user_model()

WEBHOOK_URL = "https://discord.com/api/webhooks/123/abc"


def _make_user(name="user1", email="user1@example.com"):
    """既存 setUp の呼び出し名互換 wrapper。新規テストは make_user を直接使う。"""
    return make_user(user_name=name, email=email)


def _make_user_without_email(name):
    """email 未設定ユーザーを作る.

    ``create_user`` は email を必須として弾くため、作成後に queryset.update で
    空にして「移行前から email が空のまま残っているユーザー」を再現する。
    """
    user = make_user(user_name=name, email=f"{name}@example.com")
    User.objects.filter(pk=user.pk).update(email="")
    user.refresh_from_db()
    return user


def _make_community(owner=None, webhook_url=""):
    """既存 setUp の呼び出し名互換 wrapper。新規テストは make_community を直接使う。"""
    return _make_community_factory(
        owner=owner,
        status="approved",
        webhook_url=webhook_url,
    )


def _make_event(community):
    """既存 setUp の呼び出し名互換 wrapper。"""
    return make_event(community)


def _make_event_detail(event, applicant=None, status="pending"):
    """既存 setUp の呼び出し名互換 wrapper。"""
    return make_event_detail(event, applicant=applicant, status=status)


@override_settings(DEFAULT_FROM_EMAIL="noreply@example.com")
class NotifyOwnersOfNewApplicationTest(TestCase):
    """notify_owners_of_new_application の通知送信パス"""

    def setUp(self):
        self.owner = _make_user("owner1", "owner1@example.com")
        self.applicant = _make_user("applicant1", "applicant1@example.com")
        self.community = _make_community(owner=self.owner)
        self.event = _make_event(self.community)
        self.event_detail = _make_event_detail(self.event, applicant=self.applicant)

    def test_sends_email_to_each_owner(self):
        """主催者全員にメール送信される（1主催者でアウトボックスに1件）"""
        notify_owners_of_new_application(self.event_detail)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["owner1@example.com"])
        self.assertIn("新しい発表申請", msg.subject)
        self.assertIn(self.community.name, msg.subject)

    def test_sends_email_to_multiple_owners(self):
        """複数主催者全員にメール送信される（recipient リスト構築の検証）"""
        owner2 = _make_user("owner2", "owner2@example.com")
        CommunityMember.objects.create(
            community=self.community,
            user=owner2,
            role=CommunityMember.Role.OWNER,
        )
        notify_owners_of_new_application(self.event_detail)
        recipients = sorted([msg.to[0] for msg in mail.outbox])
        self.assertEqual(recipients, ["owner1@example.com", "owner2@example.com"])

    def test_skips_owner_without_email(self):
        """email 未設定の主催者は skip される（silent failure 検出）"""
        owner_no_email = _make_user_without_email("owner_no_email")
        CommunityMember.objects.create(
            community=self.community,
            user=owner_no_email,
            role=CommunityMember.Role.OWNER,
        )
        notify_owners_of_new_application(self.event_detail)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["owner1@example.com"])

    def test_returns_early_when_no_owners(self):
        """主催者がいない場合はメール送信されず早期 return する"""
        community_no_owner = _make_community(owner=None)
        event = _make_event(community_no_owner)
        event_detail = _make_event_detail(event, applicant=self.applicant)
        notify_owners_of_new_application(event_detail)
        self.assertEqual(len(mail.outbox), 0)

    @patch("event.notifications.send_mail")
    def test_swallows_send_mail_exception(self, mock_send_mail):
        """send_mail 例外時もクラッシュせず後続処理（Discord）に進む"""
        mock_send_mail.side_effect = RuntimeError("SMTP down")
        # 例外を投げずに完了する
        notify_owners_of_new_application(self.event_detail)
        mock_send_mail.assert_called_once()

    @patch("event.notifications.post_discord_webhook")
    def test_calls_discord_webhook_when_url_set(self, mock_post):
        """webhook_url 設定済みなら Discord 通知が呼ばれる"""
        self.community.notification_webhook_url = WEBHOOK_URL
        self.community.save()
        mock_post.return_value = MagicMock(ok=True, status_code=200)
        notify_owners_of_new_application(self.event_detail)
        mock_post.assert_called_once()
        # 第1引数が webhook_url であること
        self.assertEqual(mock_post.call_args.args[0], WEBHOOK_URL)


@override_settings(DEFAULT_FROM_EMAIL="noreply@example.com")
class NotifyApplicantOfResultTest(TestCase):
    """notify_applicant_of_result の承認/却下分岐"""

    def setUp(self):
        self.owner = _make_user("owner1", "owner1@example.com")
        self.applicant = _make_user("applicant1", "applicant1@example.com")
        self.community = _make_community(owner=self.owner)
        self.event = _make_event(self.community)

    def test_approved_subject_includes_approved_label(self):
        """承認時の subject に「承認」が含まれる"""
        event_detail = _make_event_detail(
            self.event, applicant=self.applicant, status="approved"
        )
        notify_applicant_of_result(event_detail)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("承認", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ["applicant1@example.com"])

    def test_approved_email_includes_slide_video_guide_url(self):
        """承認時のHTMLメールにスライド変換ガイドへのリンクが含まれる"""
        event_detail = _make_event_detail(
            self.event, applicant=self.applicant, status="approved"
        )
        notify_applicant_of_result(event_detail)

        self.assertEqual(len(mail.outbox), 1)
        html_content = mail.outbox[0].alternatives[0][0]
        self.assertIn("/guide/speaker/slide-video/", html_content)
        self.assertIn("詳しい手順はこちら", html_content)

    def test_rejected_subject_includes_rejected_label(self):
        """却下時の subject に「却下」が含まれる"""
        event_detail = _make_event_detail(
            self.event, applicant=self.applicant, status="rejected"
        )
        notify_applicant_of_result(event_detail)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("却下", mail.outbox[0].subject)

    def test_returns_early_when_applicant_missing(self):
        """applicant が None なら早期 return"""
        event_detail = _make_event_detail(self.event, applicant=None, status="approved")
        notify_applicant_of_result(event_detail)
        self.assertEqual(len(mail.outbox), 0)

    def test_returns_early_when_applicant_email_missing(self):
        """applicant.email が空なら早期 return"""
        applicant_no_email = _make_user_without_email("applicant_no_email")
        event_detail = _make_event_detail(
            self.event, applicant=applicant_no_email, status="approved"
        )
        notify_applicant_of_result(event_detail)
        self.assertEqual(len(mail.outbox), 0)


class DiscordNotificationForNewApplicationTest(TestCase):
    """_send_discord_notification_for_new_application の Webhook 送信"""

    def setUp(self):
        self.owner = _make_user("owner1", "owner1@example.com")
        self.applicant = _make_user("applicant1", "applicant1@example.com")
        self.community = _make_community(owner=self.owner, webhook_url=WEBHOOK_URL)
        self.event = _make_event(self.community)
        self.event_detail = _make_event_detail(self.event, applicant=self.applicant)

    @patch("event.notifications.post_discord_webhook")
    def test_posts_to_webhook_when_url_set(self, mock_post):
        """webhook_url 設定時に POST される"""
        mock_post.return_value = MagicMock(ok=True, status_code=200)
        _send_discord_notification_for_new_application(self.event_detail, "https://example.com/review/1")
        mock_post.assert_called_once()
        payload = mock_post.call_args.args[1]
        # content / embeds 構造の最小検証
        self.assertIn("content", payload)
        self.assertIn("embeds", payload)

    @patch("event.notifications.post_discord_webhook")
    def test_skipped_when_webhook_url_empty(self, mock_post):
        """webhook_url 空なら POST されない"""
        self.community.notification_webhook_url = ""
        self.community.save()
        _send_discord_notification_for_new_application(self.event_detail, "https://example.com/review/1")
        mock_post.assert_not_called()

    @patch("event.notifications.post_discord_webhook")
    def test_truncates_long_additional_info(self, mock_post):
        """additional_info が 1000 文字超なら切り詰め + ... サフィックス"""
        long_text = "a" * 1500
        self.event_detail.additional_info = long_text
        self.event_detail.save()
        mock_post.return_value = MagicMock(ok=True, status_code=200)
        _send_discord_notification_for_new_application(self.event_detail, "https://example.com/review/1")
        payload = mock_post.call_args.args[1]
        additional_field = next(
            (f for f in payload["embeds"][0]["fields"] if "追加情報" in f["name"]),
            None,
        )
        self.assertIsNotNone(additional_field)
        # 1000文字 + "..." で 1003文字
        self.assertTrue(additional_field["value"].endswith("..."))
        self.assertEqual(len(additional_field["value"]), 1003)

    @patch("event.notifications.post_discord_webhook")
    def test_swallows_request_exception(self, mock_post):
        """gateway最終失敗時も呼び出し元処理を継続する."""
        mock_post.side_effect = requests.RequestException("network down")
        _send_discord_notification_for_new_application(
            self.event_detail,
            "https://example.com/review/1",
        )
        mock_post.assert_called_once()

    @patch("event.notifications.post_discord_webhook")
    def test_handles_non_ok_response(self, mock_post):
        """gatewayのHTTP最終失敗後も呼び出し元は継続する."""
        mock_post.side_effect = requests.HTTPError(
            "server error",
            response=MagicMock(status_code=500),
        )
        _send_discord_notification_for_new_application(
            self.event_detail,
            "https://example.com/review/1",
        )
        mock_post.assert_called_once()


class DiscordNotificationForResultTest(TestCase):
    """_send_discord_notification_for_result の承認/却下分岐"""

    def setUp(self):
        self.owner = _make_user("owner1", "owner1@example.com")
        self.applicant = _make_user("applicant1", "applicant1@example.com")
        self.community = _make_community(owner=self.owner, webhook_url=WEBHOOK_URL)
        self.event = _make_event(self.community)

    @patch("event.notifications.post_discord_webhook")
    def test_approved_uses_green_color(self, mock_post):
        """承認時の embed color が緑 (5763719)"""
        event_detail = _make_event_detail(self.event, applicant=self.applicant, status="approved")
        mock_post.return_value = MagicMock(ok=True, status_code=200)
        _send_discord_notification_for_result(event_detail)
        payload = mock_post.call_args.args[1]
        self.assertEqual(payload["embeds"][0]["color"], 5763719)
        self.assertIn("✅", payload["embeds"][0]["title"])

    @patch("event.notifications.post_discord_webhook")
    def test_rejected_uses_red_color_and_includes_reason(self, mock_post):
        """却下時の embed color が赤 (15548997)、却下理由が fields に含まれる"""
        event_detail = _make_event_detail(self.event, applicant=self.applicant, status="rejected")
        event_detail.rejection_reason = "テーマが要件に合致しません"
        event_detail.save()
        mock_post.return_value = MagicMock(ok=True, status_code=200)
        _send_discord_notification_for_result(event_detail)
        payload = mock_post.call_args.args[1]
        self.assertEqual(payload["embeds"][0]["color"], 15548997)
        self.assertIn("❌", payload["embeds"][0]["title"])
        reason_field = next(
            (f for f in payload["embeds"][0]["fields"] if "却下理由" in f["name"]),
            None,
        )
        self.assertIsNotNone(reason_field)
        self.assertEqual(reason_field["value"], "テーマが要件に合致しません")

    @patch("event.notifications.post_discord_webhook")
    def test_skipped_when_webhook_url_empty(self, mock_post):
        """webhook_url 空なら POST されない"""
        self.community.notification_webhook_url = ""
        self.community.save()
        event_detail = _make_event_detail(self.event, applicant=self.applicant, status="approved")
        _send_discord_notification_for_result(event_detail)
        mock_post.assert_not_called()


class DiscordWebhookSafeLoggingTest(TestCase):
    """event.notifications の3つのWebhook経路で安全な最終ログを検証する."""

    def setUp(self):
        self.owner = _make_user("owner1", "owner1@example.com")
        self.applicant = _make_user("applicant1", "applicant1@example.com")
        self.community = _make_community(
            owner=self.owner,
            webhook_url=WEBHOOK_URL,
        )
        self.event = _make_event(self.community)
        self.event_detail = _make_event_detail(
            self.event,
            applicant=self.applicant,
            status="approved",
        )

    @patch("event.notifications.post_discord_webhook")
    def test_all_webhook_failures_exclude_url_and_exception_message(self, mock_post):
        """全3経路の最終ログからURL・token・例外本文・tracebackを除外する."""
        sensitive_url = "https://discord.com/api/webhooks/123456789/secret-token"
        senders = (
            (
                "new_application",
                lambda: _send_discord_notification_for_new_application(
                    self.event_detail,
                    "https://example.com/review/1",
                ),
            ),
            (
                "application_result",
                lambda: _send_discord_notification_for_result(self.event_detail),
            ),
            (
                "slide_published",
                lambda: notify_slide_material_published(self.event_detail),
            ),
        )
        for label, send in senders:
            with self.subTest(label=label):
                mock_post.reset_mock()
                mock_post.side_effect = requests.RequestException(
                    f"request failed for {sensitive_url}",
                )
                with self.assertLogs(
                    "event.notifications",
                    level="ERROR",
                ) as log_context:
                    send()

                logs = "\n".join(log_context.output)
                mock_post.assert_called_once()
                self.assertIn(f"community_id={self.community.pk}", logs)
                self.assertIn(f"event_detail_id={self.event_detail.pk}", logs)
                self.assertIn("error_type=RequestException", logs)
                self.assertIn("status_code=None", logs)
                self.assertNotIn(sensitive_url, logs)
                self.assertNotIn("secret-token", logs)
                self.assertNotIn("request failed", logs)
                self.assertNotIn("Traceback", logs)


class SpeakerAccountLinkedNotificationTest(TestCase):
    """notify_speaker_account_linked の同名ユーザー識別と email 非露出"""

    def setUp(self):
        self.owner = _make_user("owner-link", "owner-link@example.com")
        self.community = _make_community(owner=self.owner, webhook_url=WEBHOOK_URL)
        self.event = _make_event(self.community)
        self.event_detail = _make_event_detail(self.event)
        self.speaker = _make_user("かぶり太郎", "linked-speaker@example.com")
        self.speaker.date_joined = datetime(
            2024, 3, 9, 12, tzinfo=ZoneInfo("Asia/Tokyo")
        )
        self.speaker.save(update_fields=["date_joined"])

    @patch("event.notifications.post_discord_webhook")
    def test_content_includes_registration_date_for_disambiguation(self, mock_post):
        """同名ユーザーを区別できるよう登録日が本文に含まれる"""
        mock_post.return_value = MagicMock(ok=True, status_code=200)

        notify_speaker_account_linked(self.event_detail, self.speaker)

        payload = mock_post.call_args.args[1]
        self.assertIn(self.speaker.display_label, payload["content"])
        self.assertIn("2024/03/09", payload["content"])

    @patch("event.notifications.post_discord_webhook")
    def test_content_does_not_expose_email(self, mock_post):
        """通知本文にメールアドレスを含めない"""
        mock_post.return_value = MagicMock(ok=True, status_code=200)

        notify_speaker_account_linked(self.event_detail, self.speaker)

        payload = mock_post.call_args.args[1]
        self.assertNotIn(self.speaker.email, payload["content"])
        self.assertNotIn(self.speaker.email.split("@")[0], payload["content"])
