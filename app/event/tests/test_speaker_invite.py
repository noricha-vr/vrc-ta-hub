"""発表者アカウント招待・紐づけ機能のテスト。"""

from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.messages import get_messages
from django.core.signing import TimestampSigner
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from event.services.speaker_invite import SPEAKER_INVITE_SALT, create_invite_token
from tests.factories import (
    make_community,
    make_community_member,
    make_event,
    make_event_detail,
    make_user,
)


@override_settings(DISCORD_AUTH_REQUIRED=False)
class SpeakerInviteTests(TestCase):
    """招待URLの発行・確認・解除の振る舞いを検証する。"""

    def setUp(self):
        self.owner = make_user(
            user_name="speaker_invite_owner",
            email="speaker-invite-owner@example.com",
        )
        self.speaker = make_user(
            user_name="speaker_invite_speaker",
            email="speaker-invite-speaker@example.com",
        )
        self.other_user = make_user(
            user_name="speaker_invite_other",
            email="speaker-invite-other@example.com",
        )
        self.other_owner = make_user(
            user_name="speaker_invite_other_owner",
            email="speaker-invite-other-owner@example.com",
        )
        self.staff = make_user(
            user_name="speaker_invite_staff",
            email="speaker-invite-staff@example.com",
        )
        self.community = make_community(name="招待元集会", owner=self.owner)
        self.other_community = make_community(name="別集会", owner=self.other_owner)
        make_community_member(self.community, self.staff)
        self.event = make_event(self.community)
        self.event_detail = make_event_detail(
            self.event,
            applicant=None,
            status="approved",
            speaker="登壇者A",
            theme="署名付き招待URLの発表",
        )
        self.issue_url = reverse(
            "event:speaker_invite_issue",
            kwargs={"pk": self.event_detail.pk},
        )
        self.exchange_url = reverse("event:speaker_invite_token_exchange")
        self.confirm_url = reverse("event:speaker_link_confirm")

    def _exchange(self, token: str | None = None, *, client=None):
        invite_token = token or create_invite_token(self.event_detail)
        return (client or self.client).post(
            self.exchange_url,
            {"token": invite_token},
        )

    def _issue(self, *, detail=None, client=None, ajax=True):
        target = detail or self.event_detail
        headers = {"X-Requested-With": "XMLHttpRequest"} if ajax else {}
        return (client or self.client).post(
            reverse("event:speaker_invite_issue", kwargs={"pk": target.pk}),
            headers=headers,
        )

    def _assert_detail_redirect_with_message(self, response, text):
        detail_url = reverse("event:detail", kwargs={"pk": self.event_detail.pk})
        self.assertRedirects(response, detail_url, fetch_redirect_response=False)
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any(text in message for message in messages))
        return messages

    def test_owner_issues_fragment_invite_url_without_token_in_path(self):
        self.client.force_login(self.owner)

        response = self._issue()

        self.assertEqual(response.status_code, 200)
        parsed_url = urlparse(response.json()["invite_url"])
        self.assertEqual(parsed_url.path, self.confirm_url)
        self.assertEqual(parsed_url.query, "")
        self.assertTrue(parsed_url.fragment)
        self.assertNotIn(parsed_url.fragment, parsed_url.path)

    def test_non_ajax_invite_issuance_redirects_with_message(self):
        self.client.force_login(self.owner)

        response = self._issue(ajax=False)

        messages = self._assert_detail_redirect_with_message(
            response,
            "JavaScriptを有効にして",
        )
        self.assertFalse(any("/event/speaker-link/#" in message for message in messages))

    def test_non_ajax_business_error_redirects_with_message(self):
        self.event_detail.applicant = self.other_user
        self.event_detail.save(update_fields=["applicant"])
        self.client.force_login(self.owner)

        response = self._issue(ajax=False)

        self._assert_detail_redirect_with_message(response, "既にアカウント")

    def test_sensitive_responses_disable_cache_and_referrer(self):
        self.client.force_login(self.owner)
        issue_response = self._issue()
        token = urlparse(issue_response.json()["invite_url"]).fragment
        exchange_response = self._exchange(token)

        self.client.force_login(self.speaker)
        confirm_response = self.client.get(self.confirm_url)

        for response in (issue_response, exchange_response, confirm_response):
            with self.subTest(path=response.request["PATH_INFO"]):
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")
                self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")

    def test_bootstrap_page_clears_fragment_without_loading_analytics(self):
        token = create_invite_token(self.event_detail)

        response = self.client.get(self.confirm_url)

        self.assertNotContains(response, token)
        self.assertContains(response, self.exchange_url)
        self.assertContains(response, 'role="status" aria-live="polite"')
        self.assertContains(response, "このページの表示には JavaScript が必要です")
        self.assertNotContains(response, "googletagmanager")

    def test_invite_issuance_requires_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)

        response = csrf_client.post(self.issue_url)

        self.assertEqual(response.status_code, 403)

    def test_exchange_confirmation_and_unlink_require_csrf_token(self):
        token = create_invite_token(self.event_detail)

        exchange_client = Client(enforce_csrf_checks=True)
        exchange_client.get(self.confirm_url)
        exchange_response = exchange_client.post(self.exchange_url, {"token": token})
        self.assertEqual(exchange_response.status_code, 403)

        confirm_client = Client(enforce_csrf_checks=True)
        confirm_client.force_login(self.speaker)
        bootstrap = confirm_client.get(self.confirm_url)
        csrf_token = bootstrap.cookies["csrftoken"].value
        exchange_response = confirm_client.post(
            self.exchange_url,
            {"token": token},
            headers={"X-CSRFToken": csrf_token},
        )
        self.assertEqual(exchange_response.status_code, 200)
        self.assertEqual(confirm_client.post(self.confirm_url).status_code, 403)

        self.event_detail.applicant = self.speaker
        self.event_detail.save(update_fields=["applicant", "updated_at"])
        unlink_client = Client(enforce_csrf_checks=True)
        unlink_client.force_login(self.owner)
        unlink_url = reverse(
            "event:speaker_link_unlink",
            kwargs={"pk": self.event_detail.pk},
        )
        self.assertEqual(unlink_client.post(unlink_url).status_code, 403)

    def test_logged_in_speaker_exchanges_previews_and_confirms(self):
        self.client.force_login(self.speaker)

        exchange_response = self._exchange()
        preview = self.client.get(self.confirm_url)
        response = self.client.post(self.confirm_url)

        self.assertEqual(exchange_response.status_code, 200)
        self.assertEqual(exchange_response.json(), {"confirm_url": self.confirm_url})
        self.assertContains(preview, "署名付き招待URLの発表")
        self.assertContains(preview, "登壇者A")
        self.assertContains(preview, self.speaker.display_label)
        self.assertContains(preview, "「自分の発表」ページからこの発表を編集できます")
        self.assertRedirects(
            response,
            reverse("event:my_presentations"),
        )
        self.event_detail.refresh_from_db()
        self.assertEqual(self.event_detail.applicant, self.speaker)

    def test_confirmation_notifies_webhook_only_once_after_duplicate_post(self):
        webhook_url = "https://discord.example.invalid/webhook"
        self.community.notification_webhook_url = webhook_url
        self.community.save(update_fields=["notification_webhook_url"])
        self.client.force_login(self.speaker)
        self._exchange()

        with patch("event.notifications.post_discord_webhook") as webhook_mock:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(self.confirm_url)
                duplicate_response = self.client.post(self.confirm_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(duplicate_response.status_code, 200)
        webhook_mock.assert_called_once_with(
            webhook_url,
            {
                "content": (
                    "署名付き招待URLの発表 に "
                    f"{self.speaker.display_label} がアカウントを紐づけました"
                ),
                "allowed_mentions": {"parse": []},
            },
        )

    def test_confirmation_skips_discord_notification_without_webhook(self):
        self.client.force_login(self.speaker)
        self._exchange()

        with patch("event.notifications.post_discord_webhook") as webhook_mock:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(self.confirm_url)

        self.assertEqual(response.status_code, 302)
        webhook_mock.assert_not_called()

    def test_webhook_failure_does_not_rollback_confirmation(self):
        self.community.notification_webhook_url = "https://discord.example.invalid/webhook"
        self.community.save(update_fields=["notification_webhook_url"])
        self.client.force_login(self.speaker)
        self._exchange()

        with patch(
            "event.notifications.post_discord_webhook",
            side_effect=RuntimeError("webhook failure"),
        ):
            with self.assertLogs("event.notifications", level="WARNING"):
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(self.confirm_url)

        self.assertEqual(response.status_code, 302)
        self.event_detail.refresh_from_db()
        self.assertEqual(self.event_detail.applicant, self.speaker)

    def test_confirmation_offers_nondestructive_exit_and_submit_form(self):
        self.client.force_login(self.speaker)
        self._exchange()

        response = self.client.get(self.confirm_url)

        detail_url = reverse("event:detail", kwargs={"pk": self.event_detail.pk})
        self.assertContains(response, "今は紐づけない")
        self.assertContains(response, f'href="{detail_url}"')
        self.assertContains(response, "別のアカウントに紐づける場合")
        self.assertNotContains(response, f'action="{reverse("account:logout")}"')
        self.assertContains(response, 'id="speaker-link-confirm-form"')
        self.assertContains(response, "このアカウントに紐づける")

    def test_expired_token_is_rejected_during_exchange(self):
        expired_at = timezone.now() - timedelta(days=8)
        with patch("django.core.signing.time.time", return_value=expired_at.timestamp()):
            token = create_invite_token(self.event_detail)

        response = self._exchange(token)

        self.assertEqual(response.status_code, 400)
        self.assertIn("有効期限", response.json()["error"])
        self.event_detail.refresh_from_db()
        self.assertIsNone(self.event_detail.applicant)

    def test_tampered_token_is_rejected_during_exchange(self):
        token = f"{create_invite_token(self.event_detail)}tampered"

        response = self._exchange(token)

        self.assertEqual(response.status_code, 400)
        self.assertIn("正しくありません", response.json()["error"])
        self.assertNotIn(token, response.request["PATH_INFO"])
        self.event_detail.refresh_from_db()
        self.assertIsNone(self.event_detail.applicant)

    def test_legacy_updated_at_token_is_rejected_during_exchange(self):
        payload = f"{self.event_detail.pk}|{self.event_detail.updated_at.isoformat()}"
        token = TimestampSigner(salt=SPEAKER_INVITE_SALT).sign(payload)

        response = self._exchange(token)

        self.assertEqual(response.status_code, 400)
        self.assertIn("紐づけ状態", response.json()["error"])
        self.event_detail.refresh_from_db()
        self.assertIsNone(self.event_detail.applicant)

    def test_already_linked_presentation_rejects_exchange(self):
        token = create_invite_token(self.event_detail)
        self.event_detail.applicant = self.other_user
        self.event_detail.save(update_fields=["applicant", "updated_at"])

        response = self._exchange(token)

        self.assertEqual(response.status_code, 400)
        self.assertIn("紐づけ状態", response.json()["error"])
        self.event_detail.refresh_from_db()
        self.assertEqual(self.event_detail.applicant, self.other_user)

    def test_already_linked_presentation_rejects_invite_issuance(self):
        self.event_detail.applicant = self.other_user
        self.event_detail.save(update_fields=["applicant", "updated_at"])
        self.client.force_login(self.owner)

        response = self._issue()

        self.assertEqual(response.status_code, 409)
        self.assertIn("既にアカウント", response.json()["error"])

    def test_anonymous_exchange_redirects_confirm_to_login_with_generic_next(self):
        exchange_response = self._exchange()

        response = self.client.get(self.confirm_url)

        self.assertEqual(exchange_response.status_code, 200)
        self.assertEqual(response.status_code, 302)
        parsed_url = urlparse(response.url)
        self.assertEqual(parsed_url.path, reverse("account:login"))
        self.assertEqual(parse_qs(parsed_url.query)["next"], [self.confirm_url])
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

        self.client.force_login(self.speaker)
        preview = self.client.get(self.confirm_url)
        self.assertContains(preview, "署名付き招待URLの発表")

    def test_anonymous_confirmation_post_redirects_to_login(self):
        self._exchange()

        response = self.client.post(self.confirm_url)

        self.assertEqual(response.status_code, 302)
        parsed_url = urlparse(response.url)
        self.assertEqual(parsed_url.path, reverse("account:login"))
        self.assertEqual(parse_qs(parsed_url.query)["next"], [self.confirm_url])

    def test_non_owner_and_other_community_owner_cannot_issue_invite(self):
        for user in (self.other_user, self.other_owner):
            with self.subTest(user=user.user_name):
                self.client.force_login(user)
                response = self.client.post(self.issue_url)
                self.assertEqual(response.status_code, 403)
                self.assertNotEqual(response.headers["Content-Type"], "application/json")

    def test_community_staff_can_issue_invite_and_see_ui(self):
        self.client.force_login(self.staff)

        issue_response = self._issue()
        detail_response = self.client.get(
            reverse("event:detail", kwargs={"pk": self.event_detail.pk})
        )

        self.assertEqual(issue_response.status_code, 200)
        self.assertContains(detail_response, 'id="speaker-invite-form"')

    def test_superuser_without_community_membership_cannot_issue_invite(self):
        superuser = make_user(
            user_name="speaker_invite_superuser",
            email="speaker-invite-superuser@example.com",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(superuser)

        response = self.client.post(self.issue_url)

        self.assertEqual(response.status_code, 403)

    def test_invite_ui_is_hidden_from_other_community_owner(self):
        self.client.force_login(self.other_owner)

        response = self.client.get(
            reverse("event:detail", kwargs={"pk": self.event_detail.pk})
        )

        self.assertNotContains(response, 'id="speaker-invite-form"')

    def test_non_invitable_details_are_rejected_by_issue_and_exchange(self):
        pending_detail = make_event_detail(
            self.event,
            status="pending",
            theme="承認待ち発表",
        )
        special_detail = make_event_detail(
            self.event,
            status="approved",
            detail_type="SPECIAL",
            theme="特別企画",
        )
        self.client.force_login(self.owner)

        for detail in (pending_detail, special_detail):
            with self.subTest(detail=detail.theme):
                issue_response = self._issue(detail=detail)
                exchange_response = self._exchange(create_invite_token(detail))
                self.assertEqual(issue_response.status_code, 400)
                self.assertEqual(exchange_response.status_code, 400)
                self.assertIn("承認済みの発表", exchange_response.json()["error"])

    def test_owner_can_unlink_speaker_account(self):
        self.event_detail.applicant = self.speaker
        self.event_detail.save(update_fields=["applicant", "updated_at"])
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("event:speaker_link_unlink", kwargs={"pk": self.event_detail.pk})
        )

        self.assertRedirects(
            response,
            reverse("event:detail", kwargs={"pk": self.event_detail.pk}),
        )
        self.event_detail.refresh_from_db()
        self.assertIsNone(self.event_detail.applicant)

    def test_non_owner_cannot_unlink_speaker_account(self):
        self.event_detail.applicant = self.speaker
        self.event_detail.save(update_fields=["applicant", "updated_at"])
        self.client.force_login(self.other_owner)

        response = self.client.post(
            reverse("event:speaker_link_unlink", kwargs={"pk": self.event_detail.pk})
        )

        self.assertEqual(response.status_code, 403)
        self.event_detail.refresh_from_db()
        self.assertEqual(self.event_detail.applicant, self.speaker)

    def test_old_token_can_be_used_again_after_link_and_unlink(self):
        old_token = create_invite_token(self.event_detail)
        self.client.force_login(self.speaker)
        self._exchange(old_token)
        self.client.post(self.confirm_url)

        self.client.force_login(self.owner)
        self.client.post(
            reverse("event:speaker_link_unlink", kwargs={"pk": self.event_detail.pk})
        )
        self.client.force_login(self.speaker)
        exchange_response = self._exchange(old_token)
        confirm_response = self.client.post(self.confirm_url)

        self.assertEqual(exchange_response.status_code, 200)
        self.assertEqual(confirm_response.status_code, 302)
        self.event_detail.refresh_from_db()
        self.assertEqual(self.event_detail.applicant, self.speaker)

    def test_token_remains_valid_after_presentation_text_update(self):
        token = create_invite_token(self.event_detail)
        self.event_detail.theme = "修正後の発表テーマ"
        self.event_detail.save(update_fields=["theme", "updated_at"])
        self.client.force_login(self.speaker)

        exchange_response = self._exchange(token)
        preview_response = self.client.get(self.confirm_url)

        self.assertEqual(exchange_response.status_code, 200)
        self.assertContains(preview_response, "修正後の発表テーマ")

    def test_confirmation_error_does_not_show_confirmation_copy(self):
        token = create_invite_token(self.event_detail)
        self._exchange(token)
        self.event_detail.applicant = self.other_user
        self.event_detail.save(update_fields=["applicant"])
        self.client.force_login(self.speaker)

        response = self.client.get(self.confirm_url)

        self.assertContains(response, "紐づけ状態")
        self.assertContains(response, 'role="alert" aria-live="assertive"')
        self.assertNotContains(response, "ログイン中アカウントに紐づけます")

    def test_linked_speaker_sees_my_presentations_link_and_can_open_edit_form(self):
        self.client.force_login(self.speaker)
        self._exchange()
        link_response = self.client.post(self.confirm_url)
        self.assertEqual(link_response.status_code, 302)

        edit_url = reverse(
            "event:detail_update",
            kwargs={"pk": self.event_detail.pk},
        )
        my_page_response = self.client.get(reverse("event:my_presentations"))

        self.assertContains(my_page_response, "自分の発表")
        self.assertContains(my_page_response, "署名付き招待URLの発表")
        self.assertContains(my_page_response, edit_url)
        self.assertNotContains(my_page_response, "イベント管理:")
        self.assertNotContains(my_page_response, "集会未登録")
        self.assertNotContains(my_page_response, "イベントがありません")
        self.assertEqual(
            list(my_page_response.context["presentations"]),
            [self.event_detail],
        )

        edit_response = self.client.get(edit_url)

        self.assertEqual(edit_response.status_code, 200)
        self.assertContains(edit_response, "data-article-generation-form")
        self.assertEqual(edit_response.context["form"].instance, self.event_detail)
        self.assertEqual(
            edit_response.context["form"]["theme"].value(),
            "署名付き招待URLの発表",
        )
