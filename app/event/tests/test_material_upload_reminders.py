from datetime import date, time, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase

from community.models import Community
from event.material_upload_reminders import (
    MATERIAL_UPLOAD_HISTORY_URL,
    MaterialReminderDecision,
    parse_material_reminder_decision,
    send_material_upload_reminders,
)
from event.models import Event, EventDetail, MaterialUploadReminderLog
from event.tests.tweet_generation import TweetGenerationPatchMixin
from vket.models import VketCollaboration, VketParticipation, VketPresentation

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


class MaterialUploadReminderTest(TweetGenerationPatchMixin, TestCase):
    def setUp(self):
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


class MaterialReminderDecisionParseTest(TestCase):
    def test_parse_json_with_surrounding_text(self):
        decision = parse_material_reminder_decision(
            '結果: {"shouldSend": false, "confidence": "high", '
            '"reason": "動画公開なし", "matchedIntent": "no_public_material_or_video"}'
        )

        self.assertFalse(decision.should_send)
        self.assertEqual(decision.matched_intent, "no_public_material_or_video")
