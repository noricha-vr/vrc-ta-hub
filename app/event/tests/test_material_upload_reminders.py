from datetime import date, time, timedelta
from io import StringIO
from unittest.mock import Mock, patch

import requests
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from community.models import BotInstallation, Community
from event.material_upload_reminders import (
    MATERIAL_UPLOAD_HISTORY_URL,
    MaterialReminderDecision,
    _send_material_upload_reminder_discord_dm,
    parse_material_reminder_decision,
    send_material_upload_reminders,
)
from event.models import Event, EventDetail, MaterialUploadReminderLog
from vket.models import VketCollaboration, VketParticipation, VketPresentation
from website.constants import build_site_url

User = get_user_model()


class StubDecisionService:
    def __init__(self, mapping=None, default_should_send=True, raise_error=False):
        self.mapping = mapping or {}
        self.default_should_send = default_should_send
        self.raise_error = raise_error
        self.seen_notes = []

    def decide(self, note_text: str) -> MaterialReminderDecision:
        if self.raise_error:
            raise ValueError("llm_error")
        self.seen_notes.append(note_text)
        should_send = self.mapping.get(note_text, self.default_should_send)
        return MaterialReminderDecision(
            shouldSend=should_send,
            confidence="high",
            reason="送信可" if should_send else "公開しない意向がある",
            matchedIntent="none" if should_send else "no_public_material_or_video",
        )


class MaterialUploadReminderTest(TestCase):
    def setUp(self):
        self.discord_post = self.enterContext(patch('website.discord_delivery.requests.post'))
        self.target_date = date(2026, 6, 4)
        self.community = Community.objects.create(
            name="ReminderCommunity",
            start_time=time(22, 0),
            duration=60,
            weekdays=["Thu"],
            frequency="Every week",
            organizers="owner",
            status="approved",
        )
        self.event = Event.objects.create(
            community=self.community,
            date=self.target_date,
            start_time=time(22, 0),
            duration=60,
            weekday="Thu",
        )

    def create_user(self, suffix: str, display_name: str = ""):
        return User.objects.create_user(
            user_name=f"user_{suffix}",
            display_name=display_name,
            email=f"{suffix}@example.com",
            password="pw",
        )

    def create_detail(self, suffix: str, additional_info: str = "", display_name: str = "表示名"):
        user = self.create_user(suffix, display_name=display_name)
        return EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            applicant=user,
            speaker=display_name or user.user_name,
            theme=f"テーマ{suffix}",
            start_time=time(22, 0),
            duration=30,
            additional_info=additional_info,
        )

    def test_additional_info_no_public_material_cases_are_skipped(self):
        cases = [
            "資料公開なし",
            "スライド非公開",
            "YouTube公開なし",
            "動画公開なし",
            "Youtube｜動画 公開なし。",
        ]
        service = StubDecisionService(mapping={case: False for case in cases})
        for index, text in enumerate(cases):
            self.create_detail(str(index), additional_info=text)

        results = send_material_upload_reminders(
            target_date=self.target_date,
            decision_service=service,
        )

        self.assertEqual([result.action for result in results], ["skipped_by_note"] * len(cases))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(MaterialUploadReminderLog.objects.count(), len(cases))

    def test_limited_expressions_can_send_material_request(self):
        cases = [
            "資料は後日公開予定",
            "動画は公開なしだがスライドは共有可",
        ]
        service = StubDecisionService(mapping={case: True for case in cases})
        for index, text in enumerate(cases):
            self.create_detail(f"limited{index}", additional_info=text)

        results = send_material_upload_reminders(
            target_date=self.target_date,
            decision_service=service,
        )

        self.assertEqual([result.action for result in results], ["sent", "sent"])
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            MaterialUploadReminderLog.objects.filter(status=MaterialUploadReminderLog.Status.SENT).count(),
            2,
        )

    def test_vket_organizer_note_is_used_for_decision(self):
        detail = self.create_detail("vket", additional_info="資料は後日公開予定")
        detail.applicant = None
        detail.save(update_fields=["applicant"])
        vket_applicant = self.create_user("vket_applicant", display_name="Vket申請者")
        collaboration = VketCollaboration.objects.create(
            slug="vket-reminder",
            name="Vket Reminder",
            period_start=self.target_date,
            period_end=self.target_date + timedelta(days=1),
            registration_deadline=self.target_date - timedelta(days=10),
            lt_deadline=self.target_date - timedelta(days=5),
        )
        participation = VketParticipation.objects.create(
            collaboration=collaboration,
            community=self.community,
            organizer_note="Youtube｜動画 公開なし。",
            applied_by=vket_applicant,
        )
        VketPresentation.objects.create(
            participation=participation,
            speaker="vket speaker",
            theme="vket theme",
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=detail,
        )
        service = StubDecisionService(mapping={"Youtube｜動画 公開なし。": False})

        results = send_material_upload_reminders(
            target_date=self.target_date,
            decision_service=service,
        )

        self.assertEqual(results[0].action, "skipped_by_note")
        self.assertEqual(results[0].email, "vket_applicant@example.com")
        self.assertEqual(service.seen_notes, ["Youtube｜動画 公開なし。"])
        self.assertEqual(len(mail.outbox), 0)

    def test_llm_error_does_not_send_or_create_log(self):
        self.create_detail("error", additional_info="判定できない文章")

        results = send_material_upload_reminders(
            target_date=self.target_date,
            decision_service=StubDecisionService(raise_error=True),
        )

        self.assertEqual(results[0].action, "llm_error")
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(MaterialUploadReminderLog.objects.count(), 0)

    def test_email_uses_display_name_and_required_body(self):
        detail = self.create_detail("mail", display_name="発表者表示名")

        send_material_upload_reminders(
            target_date=self.target_date,
            decision_service=StubDecisionService(),
        )

        message = mail.outbox[0]
        self.assertEqual(message.subject, "発表資料アップロードのお願い")
        self.assertIn("発表者表示名 さん", message.body)
        self.assertIn(MATERIAL_UPLOAD_HISTORY_URL, message.body)
        self.assertIn(f"/account/lt-applications/{detail.pk}/edit/", message.body)
        self.assertNotIn("締切", message.body)

    def test_vket_recipient_can_open_edit_url_from_email(self):
        detail = self.create_detail("vket_link")
        recipient = detail.applicant
        detail.applicant = None
        detail.save(update_fields=["applicant"])
        collaboration = VketCollaboration.objects.create(
            slug="vket-reminder-link",
            name="Vket Reminder Link",
            period_start=self.target_date,
            period_end=self.target_date + timedelta(days=1),
            registration_deadline=self.target_date - timedelta(days=10),
            lt_deadline=self.target_date - timedelta(days=5),
        )
        participation = VketParticipation.objects.create(
            collaboration=collaboration,
            community=self.community,
            applied_by=recipient,
        )
        VketPresentation.objects.create(
            participation=participation,
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=detail,
        )

        send_material_upload_reminders(
            target_date=self.target_date,
            decision_service=StubDecisionService(),
        )

        edit_path = reverse("account:lt_application_edit", kwargs={"pk": detail.pk})
        self.assertIn(edit_path, mail.outbox[0].body)
        self.client.force_login(recipient)
        self.assertEqual(self.client.get(edit_path).status_code, 200)

    def test_email_falls_back_to_display_label_when_display_name_is_blank(self):
        self.create_detail("fallback", display_name="")

        send_material_upload_reminders(
            target_date=self.target_date,
            decision_service=StubDecisionService(),
        )

        self.assertIn("user_fallback さん", mail.outbox[0].body)

    def test_existing_log_prevents_duplicate_send(self):
        detail = self.create_detail("duplicate")

        send_material_upload_reminders(
            target_date=self.target_date,
            decision_service=StubDecisionService(),
        )
        results = send_material_upload_reminders(
            target_date=self.target_date,
            decision_service=StubDecisionService(),
        )

        self.assertEqual(results, [])
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(MaterialUploadReminderLog.objects.filter(event_detail=detail).exists())

    def test_dry_run_outputs_result_without_sending_or_logging(self):
        self.create_detail("dryrun")

        output = StringIO()
        with self.settings(
            MATERIAL_REMINDER_DECISION_SERVICE=StubDecisionService()
        ):
            call_command(
                "send_post_event_material_upload_reminders",
                "--date",
                "2026-06-04",
                "--dry-run",
                stdout=output,
            )

        self.assertIn("dry_run=True total=1", output.getvalue())
        self.assertIn("action=would_send", output.getvalue())
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(MaterialUploadReminderLog.objects.count(), 0)

    def prepare_discord_dm(self):
        detail = self.create_detail('discord_dm')
        self.uid = '8' * 18
        self.channel_id = '7' * 18
        SocialAccount.objects.create(user=detail.applicant, provider='discord', uid=self.uid)
        BotInstallation.objects.create(
            community=self.community, guild_id='6' * 18, status='configured',
            notification_channel_id='5' * 18,
        )
        self.enterContext(override_settings(
            DISCORD_BOT_TOKEN='synthetic-bot-secret',
            DISCORD_BOT_DELIVERY_COMMUNITY_IDS=str(self.community.pk),
        ))
        response = Mock(status_code=200)
        response.json.return_value = {'id': self.channel_id}
        self.discord_post.side_effect = [response, Mock(status_code=200)]
        return detail

    def send_reminders(self, **kwargs):
        return send_material_upload_reminders(
            target_date=self.target_date, decision_service=StubDecisionService(), **kwargs,
        )

    def test_discord_dm_after_email_records_state_and_required_body_once(self):
        detail = self.prepare_discord_dm()
        detail.theme = '**発表** _資料_ [案内](url) `code` |spoiler|\n# 見出し @everyone'
        detail.save(update_fields=['theme'])
        channel_response = Mock(status_code=200)
        channel_response.json.return_value = {'id': self.channel_id}

        def post(*args, **kwargs):
            log = MaterialUploadReminderLog.objects.get(event_detail=detail)
            self.assertEqual(log.status, 'sent')
            self.assertEqual(log.discord_dm_status, 'sending')
            self.assertIsNone(log.discord_dm_sent_at)
            self.assertEqual(len(mail.outbox), 1)
            return channel_response

        self.discord_post.side_effect = post
        with self.assertLogs('event.material_upload_reminders', level='INFO') as captured:
            results = self.send_reminders()
        self.assertEqual(results[0].action, 'sent')
        self.assertEqual(self.discord_post.call_count, 2)
        message = self.discord_post.call_args.kwargs['json']
        self.assertEqual(message['allowed_mentions'], {'parse': []})
        body = message['content']
        self.assertIn('2026年06月04日', body)
        self.assertIn(r'\*\*発表\*\* \_資料\_ \[案内\]\(url\) \`code\` \|spoiler\| \# 見出し @everyone', body)
        self.assertIn('資料（スライドの URL か PDF）を登録してください', body)
        upload_url = build_site_url(reverse('account:lt_application_edit', kwargs={'pk': detail.pk}))
        self.assertIn(upload_url, body)
        self.assertIn(upload_url, mail.outbox[0].body)
        log = MaterialUploadReminderLog.objects.get(event_detail=detail)
        self.assertEqual(log.discord_dm_status, 'sent')
        self.assertIsNotNone(log.discord_dm_sent_at)
        self.assertEqual(self.send_reminders(), [])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.discord_post.call_count, 2)
        for value in (self.uid, self.channel_id, 'synthetic-bot-secret', upload_url):
            self.assertNotIn(value, '\n'.join(captured.output))

    def test_discord_dm_requires_linked_discord_account(self):
        detail = self.prepare_discord_dm()
        SocialAccount.objects.all().delete()
        SocialAccount.objects.create(user=detail.applicant, provider='other', uid=self.uid)
        self.send_reminders()
        self.assert_dm_not_sent(detail)

    def test_discord_dm_requires_valid_uid(self):
        detail = self.prepare_discord_dm()
        for uid in ('8' * 16, '8' * 21, '８' * 18, self.uid + '\n'):
            with self.subTest(uid=uid):
                SocialAccount.objects.filter(user=detail.applicant).update(uid=uid)
                self.send_reminders()
                self.assert_dm_not_sent(detail)
                MaterialUploadReminderLog.objects.all().delete()

    def test_discord_dm_requires_bot_delivery(self):
        detail = self.prepare_discord_dm()
        self.community.notification_webhook_url = 'https://discord.com/api/webhooks/synthetic'
        self.community.save(update_fields=['notification_webhook_url'])
        cases = (
            {'DISCORD_BOT_DELIVERY_COMMUNITY_IDS': ''},  # Webhook 経路
            {'DISCORD_BOT_TOKEN': ''},
        )
        for settings_override in cases:
            with self.subTest(settings_override=settings_override), override_settings(**settings_override):
                self.send_reminders()
                self.assert_dm_not_sent(detail)
                MaterialUploadReminderLog.objects.all().delete()
        self.community.notification_webhook_url = ''
        self.community.save(update_fields=['notification_webhook_url'])
        BotInstallation.objects.all().delete()  # 通知先なし
        self.send_reminders()
        self.assert_dm_not_sent(detail)

    def test_discord_dm_not_sent_when_note_excludes_reminder(self):
        detail = self.prepare_discord_dm()
        send_material_upload_reminders(
            target_date=self.target_date, decision_service=StubDecisionService(default_should_send=False),
        )
        self.assert_dm_not_sent(detail, email_status='skipped_by_note')
        self.assertEqual(len(mail.outbox), 0)

    def test_discord_dm_not_sent_in_dry_run(self):
        self.prepare_discord_dm()
        self.assertEqual(self.send_reminders(dry_run=True)[0].action, 'would_send')
        self.discord_post.assert_not_called()
        self.assertFalse(MaterialUploadReminderLog.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_discord_dm_not_sent_on_mail_failure(self):
        self.prepare_discord_dm()
        for outcome in (0, ValueError('mail failed')):
            with self.subTest(outcome=outcome), patch('event.material_upload_reminders.send_mail') as send:
                if isinstance(outcome, Exception):
                    send.side_effect = outcome
                else:
                    send.return_value = outcome
                with self.assertLogs('event.material_upload_reminders', level='ERROR'):
                    self.assertEqual(self.send_reminders()[0].action, 'mail_error')
                self.discord_post.assert_not_called()
                self.assertFalse(MaterialUploadReminderLog.objects.exists())

    def test_discord_dm_not_sent_for_registered_materials(self):
        detail = self.prepare_discord_dm()
        for field, value in (('slide_url', 'https://example.com/slides'), ('slide_file', 'slides.pdf')):
            with self.subTest(field=field):
                # 資料公開の既存通知を発火させず、抽出時点の登録済み資料を用意する。
                EventDetail.objects.filter(pk=detail.pk).update(slide_url='', slide_file='')
                EventDetail.objects.filter(pk=detail.pk).update(**{field: value})
                self.assertEqual(self.send_reminders(), [])
                self.discord_post.assert_not_called()
                self.assertFalse(MaterialUploadReminderLog.objects.exists())

    def test_existing_dm_states_are_never_resent_even_with_stale_log_object(self):
        detail = self.prepare_discord_dm()
        log = MaterialUploadReminderLog.objects.create(event_detail=detail, status='sent')
        for status in ('', 'sending', 'sent', 'forbidden', 'failed'):
            with self.subTest(status=status):
                MaterialUploadReminderLog.objects.filter(pk=log.pk).update(discord_dm_status=status)
                self.assertEqual(self.send_reminders(), [])
                if status:
                    _send_material_upload_reminder_discord_dm(log, detail, detail.applicant)
                self.discord_post.assert_not_called()

    def test_discord_dm_http_failure_keeps_mail_sent_and_is_not_retried(self):
        detail = self.prepare_discord_dm()
        for step in (1, 2):
            for status_code, expected in ((403, 'forbidden'), (500, 'failed'), (429, 'failed')):
                with self.subTest(step=step, status_code=status_code):
                    response = Mock(status_code=200)
                    response.json.return_value = {'id': self.channel_id}
                    failure = Mock(status_code=status_code)
                    self.discord_post.reset_mock()
                    self.discord_post.side_effect = [failure] if step == 1 else [response, failure]
                    results = self.send_reminders()
                    self.assertEqual(results[0].action, 'sent')
                    log = MaterialUploadReminderLog.objects.get(event_detail=detail)
                    self.assertEqual(log.status, 'sent')
                    self.assertIsNotNone(log.sent_at)
                    self.assertEqual(log.discord_dm_status, expected)
                    self.assertIsNone(log.discord_dm_sent_at)
                    self.assertEqual(self.send_reminders(), [])
                    self.assertEqual(self.discord_post.call_count, step)
                    MaterialUploadReminderLog.objects.all().delete()
        self.assertEqual(len(mail.outbox), 6)

    def test_discord_dm_network_failure_logs_only_presentation_and_status(self):
        detail = self.prepare_discord_dm()
        secret_text = f'synthetic-bot-secret {self.uid} https://discord.com/private'
        self.discord_post.side_effect = requests.Timeout(secret_text)
        with self.assertLogs('event.material_upload_reminders', level='INFO') as captured:
            results = self.send_reminders()
        log = MaterialUploadReminderLog.objects.get(event_detail=detail)
        self.assertEqual(log.status, 'sent')
        self.assertEqual(log.discord_dm_status, 'failed')
        self.assertIsNone(log.discord_dm_sent_at)
        self.assertEqual(results[0].action, 'sent')
        self.assertEqual(self.send_reminders(), [])
        self.discord_post.assert_called_once()
        self.assertEqual(len(mail.outbox), 1)
        for value in ('synthetic-bot-secret', self.uid, 'https://discord.com'):
            self.assertNotIn(value, '\n'.join(captured.output))
            self.assertNotIn(value, repr(results))

    def assert_dm_not_sent(self, detail, email_status='sent'):
        self.discord_post.assert_not_called()
        log = MaterialUploadReminderLog.objects.get(event_detail=detail)
        self.assertEqual(log.status, email_status)
        self.assertEqual(log.discord_dm_status, '')
        self.assertIsNone(log.discord_dm_sent_at)


class MaterialReminderDecisionParseTest(TestCase):
    def test_parse_json_with_surrounding_text(self):
        decision = parse_material_reminder_decision(
            '結果: {"shouldSend": false, "confidence": "high", '
            '"reason": "動画公開なし", "matchedIntent": "no_public_material_or_video"}'
        )

        self.assertFalse(decision.should_send)
        self.assertEqual(decision.matched_intent, "no_public_material_or_video")


@override_settings(REQUEST_TOKEN="test-token")
class MaterialUploadReminderEndpointTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse("event:send_material_upload_reminders")

    @patch("event.views.material_upload_reminder.send_material_upload_reminders")
    def test_endpoint_requires_request_token(self, mock_send):
        response = self.client.get(self.url, HTTP_REQUEST_TOKEN="wrong")

        self.assertEqual(response.status_code, 401)
        mock_send.assert_not_called()

    @patch("event.views.material_upload_reminder.send_material_upload_reminders")
    def test_endpoint_returns_result(self, mock_send):
        mock_send.return_value = [
            ReminderResultForTest(
                event_detail_id=12,
                email="speaker@example.com",
                action="sent",
                reason="送信可",
                confidence="high",
                matched_intent="none",
            )
        ]

        response = self.client.get(
            self.url,
            {"date": "2026-06-04"},
            HTTP_REQUEST_TOKEN="test-token",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["targetDate"], "2026-06-04")
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["results"][0]["action"], "sent")
        mock_send.assert_called_once_with(target_date=date(2026, 6, 4), dry_run=False)

    @patch("event.views.material_upload_reminder.send_material_upload_reminders")
    def test_endpoint_accepts_dry_run(self, mock_send):
        mock_send.return_value = []

        response = self.client.get(
            self.url,
            {"date": "2026-06-04", "dry_run": "1"},
            HTTP_REQUEST_TOKEN="test-token",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["dryRun"])
        mock_send.assert_called_once_with(target_date=date(2026, 6, 4), dry_run=True)

    def test_endpoint_rejects_invalid_date(self):
        response = self.client.get(
            self.url,
            {"date": "2026/06/04"},
            HTTP_REQUEST_TOKEN="test-token",
        )

        self.assertEqual(response.status_code, 400)


class ReminderResultForTest:
    def __init__(self, *, event_detail_id, email, action, reason, confidence="", matched_intent=""):
        self.event_detail_id = event_detail_id
        self.email = email
        self.action = action
        self.reason = reason
        self.confidence = confidence
        self.matched_intent = matched_intent
