from datetime import date, datetime, time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from community.models import Community
from event.models import Event, EventDetail, MaterialUploadReminderLog
from event.slide_reminders import process_slide_publication_reminders
from vket.models import VketCollaboration, VketParticipation, VketPresentation

User = get_user_model()


class SlideReminderTestCase(TestCase):
    def setUp(self):
        self.now = timezone.make_aware(datetime(2026, 6, 5, 12, 0))
        self.applicant = User.objects.create_user(
            user_name='speaker',
            display_name='発表者',
            email='speaker@example.com',
            password='pass',
        )
        self.community = Community.objects.create(
            name='テスト集会',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Fri'],
            frequency='毎週',
            organizers='owner',
            status='approved',
        )

    def create_detail(self, *, event_date=None, reminder_status=None, **kwargs):
        event = Event.objects.create(
            community=self.community,
            date=event_date or date(2026, 5, 29),
            start_time=time(22, 0),
            duration=60,
            weekday='Fri',
        )
        defaults = {
            'event': event,
            'detail_type': 'LT',
            'status': 'approved',
            'applicant': self.applicant,
            'speaker': '発表者',
            'theme': 'スキーマ時代',
            'start_time': time(22, 0),
            'duration': 15,
        }
        defaults.update(kwargs)
        detail = EventDetail.objects.create(**defaults)
        MaterialUploadReminderLog.objects.create(
            event_detail=detail,
            status=reminder_status or MaterialUploadReminderLog.Status.SENT,
            sent_at=self.now,
        )
        return detail

    def test_process_sends_email_for_approved_lt_without_material_after_one_week(self):
        detail = self.create_detail()

        result = process_slide_publication_reminders(now=self.now)

        self.assertEqual(result.sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('発表資料公開のお願い', mail.outbox[0].subject)
        self.assertIn('スキーマ時代', mail.outbox[0].body)
        self.assertIn(reverse('account:lt_application_edit', kwargs={'pk': detail.pk}), mail.outbox[0].body)
        detail.material_upload_reminder_log.refresh_from_db()
        self.assertIsNotNone(detail.material_upload_reminder_log.follow_up_sent_at)

    def test_vket_recipient_can_open_edit_url_from_email(self):
        detail = self.create_detail(applicant=None)
        collaboration = VketCollaboration.objects.create(
            slug='slide-reminder-vket',
            name='Slide Reminder Vket',
            period_start=date(2026, 5, 29),
            period_end=date(2026, 5, 30),
            registration_deadline=date(2026, 5, 1),
            lt_deadline=date(2026, 5, 15),
        )
        participation = VketParticipation.objects.create(
            collaboration=collaboration,
            community=self.community,
            applied_by=self.applicant,
        )
        VketPresentation.objects.create(
            participation=participation,
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=detail,
        )

        result = process_slide_publication_reminders(now=self.now)

        self.assertEqual(result.sent, 1)
        edit_path = reverse('account:lt_application_edit', kwargs={'pk': detail.pk})
        self.assertIn(edit_path, mail.outbox[0].body)
        self.client.force_login(self.applicant)
        self.assertEqual(self.client.get(edit_path).status_code, 200)

    def test_process_does_not_send_twice(self):
        self.create_detail()

        first = process_slide_publication_reminders(now=self.now)
        second = process_slide_publication_reminders(now=self.now)

        self.assertEqual(first.sent, 1)
        self.assertEqual(second.candidates, 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_process_skips_before_one_week(self):
        self.create_detail(event_date=date(2026, 5, 30))

        result = process_slide_publication_reminders(now=self.now)

        self.assertEqual(result.candidates, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_process_skips_when_initial_reminder_was_skipped_by_note(self):
        self.create_detail(reminder_status=MaterialUploadReminderLog.Status.SKIPPED_BY_NOTE)

        result = process_slide_publication_reminders(now=self.now)

        self.assertEqual(result.candidates, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_process_skips_when_material_exists(self):
        self.create_detail(event_date=date(2026, 5, 27), slide_url='https://example.com/slides')
        self.create_detail(event_date=date(2026, 5, 28), slide_file='slide/test.pdf')
        self.create_detail(event_date=date(2026, 5, 29), youtube_url='https://youtu.be/dQw4w9WgXcQ')

        result = process_slide_publication_reminders(now=self.now)

        self.assertEqual(result.candidates, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_process_skips_without_applicant_email(self):
        self.applicant.email = ''
        self.applicant.save(update_fields=['email'])
        self.create_detail()

        result = process_slide_publication_reminders(now=self.now)

        self.assertEqual(result.candidates, 1)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_dry_run_does_not_send_or_mark_sent(self):
        detail = self.create_detail()

        result = process_slide_publication_reminders(dry_run=True, now=self.now)

        self.assertTrue(result.dry_run)
        self.assertEqual(result.candidates, 1)
        self.assertEqual(result.event_detail_ids, [detail.pk])
        self.assertEqual(len(mail.outbox), 0)
        detail.material_upload_reminder_log.refresh_from_db()
        self.assertIsNone(detail.material_upload_reminder_log.follow_up_sent_at)

    def test_management_command_dry_run_outputs_candidates(self):
        detail = self.create_detail()

        from io import StringIO
        output = StringIO()
        call_command('send_slide_reminders', '--dry-run', stdout=output)

        self.assertIn(str(detail.pk), output.getvalue())


@override_settings(REQUEST_TOKEN='test-token')
class SlideReminderEndpointTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse('event:send_slide_reminders')

    @patch('event.views.slide_reminder.process_slide_publication_reminders')
    def test_endpoint_requires_request_token(self, mock_process):
        response = self.client.get(self.url, HTTP_REQUEST_TOKEN='wrong')

        self.assertEqual(response.status_code, 401)
        mock_process.assert_not_called()

    @patch('event.views.slide_reminder.process_slide_publication_reminders')
    def test_endpoint_returns_result(self, mock_process):
        mock_process.return_value.as_dict.return_value = {
            'dryRun': False,
            'candidates': 1,
            'sent': 1,
            'skipped': 0,
            'failed': 0,
            'eventDetailIds': [1],
        }

        response = self.client.get(self.url, {'limit': '3'}, HTTP_REQUEST_TOKEN='test-token')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['sent'], 1)
        mock_process.assert_called_once_with(dry_run=False, limit=3)

    @patch('event.views.slide_reminder.process_slide_publication_reminders')
    def test_endpoint_accepts_dry_run(self, mock_process):
        mock_process.return_value.as_dict.return_value = {
            'dryRun': True,
            'candidates': 0,
            'sent': 0,
            'skipped': 0,
            'failed': 0,
            'eventDetailIds': [],
        }

        response = self.client.get(
            self.url,
            {'dry_run': '1'},
            HTTP_REQUEST_TOKEN='test-token',
        )

        self.assertEqual(response.status_code, 200)
        mock_process.assert_called_once_with(dry_run=True, limit=50)

    def test_endpoint_rejects_invalid_limit(self):
        response = self.client.get(
            self.url,
            {'limit': 'many'},
            HTTP_REQUEST_TOKEN='test-token',
        )

        self.assertEqual(response.status_code, 400)
