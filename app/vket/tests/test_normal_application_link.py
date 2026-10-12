"""通常応募の承認・本人編集・再同期・取消を同じ公開IDで扱う。"""
from datetime import time
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from event.forms import LTApplicationForm
from event.models import EventDetail
from tests.factories import make_community, make_event, make_user, make_event_detail
from vket.application_link import application_warnings, link_approved_application, application_links
from vket.auto_confirm import auto_confirm_schedules
from vket.models import VketCollaboration, VketParticipation, VketPresentation
from vket.services import confirm_participation_schedule, delete_requested_presentation, clear_participation_publication


class NormalApplicationLinkTests(TestCase):
    def setUp(self):
        self.owner = make_user('host', 'host@example.com')
        self.speaker = make_user('speaker', 'speaker@example.com')
        self.community = make_community(owner=self.owner, lt_start_offset_minutes=15)
        self.event = make_event(self.community, event_date=timezone.localdate(), start_time=time(22), duration=60)
        self.collaboration = VketCollaboration.objects.create(
            name='合成コラボ', slug='synthetic-collaboration', period_start=self.event.date,
            period_end=self.event.date, registration_deadline=self.event.date,
            lt_deadline=self.event.date, phase='entry_open',
        )
        self.participation = VketParticipation.objects.create(
            collaboration=self.collaboration, community=self.community, published_event=self.event,
            requested_date=self.event.date, confirmed_date=self.event.date,
            requested_start_time=time(22), confirmed_start_time=time(22),
            requested_duration=60, confirmed_duration=60, applied_by=self.owner, progress='rehearsal',
        )

    def detail(self, start=time(22, 15), status='approved'):
        return make_event_detail(self.event, theme='合成演題', speaker='合成登壇者',
            start_time=start, duration=30, status=status, applicant=self.speaker,
            h1='残すタイトル', contents='残す本文', youtube_url='https://www.youtube.com/watch?v=abcdefghijk')

    def test_pending_is_not_linked_or_approved_by_daily_sync(self):
        detail = self.detail(status='pending')
        link_approved_application(detail)
        auto_confirm_schedules()
        detail.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(detail.status, 'pending')
        self.assertEqual(self.event.duration, 60)
        self.assertFalse(VketPresentation.objects.exists())

    def test_approval_links_same_id_extends_75_minutes_and_is_idempotent(self):
        first = self.detail()
        second = self.detail(time(22, 45))
        link_approved_application(first)
        link_approved_application(second)
        link_approved_application(second)
        self.participation.refresh_from_db()
        confirm_participation_schedule(self.participation)
        second.refresh_from_db()
        self.event.refresh_from_db()
        self.participation.refresh_from_db()
        self.assertEqual(VketPresentation.objects.count(), 2)
        self.assertEqual(self.event.duration, 75)
        self.assertEqual((self.participation.requested_duration, self.participation.confirmed_duration), (75, 75))
        self.assertEqual(second.applicant_id, self.speaker.pk)
        self.assertEqual((second.h1, second.contents), ('残すタイトル', '残す本文'))
        self.assertEqual(second.youtube_url, 'https://www.youtube.com/watch?v=abcdefghijk')
        self.assertTrue(VketPresentation.objects.filter(published_event_detail_id=second.pk).exists())

    def test_existing_pending_ajax_approval_uses_same_record(self):
        detail = self.detail(status='pending')
        self.client.force_login(self.owner)
        with patch('event.notifications.notify_applicant_of_result'):
            response = self.client.post(reverse('event:lt_application_approve', args=[detail.pk]),
                                        HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'approved')
        self.assertEqual(VketPresentation.objects.get().published_event_detail_id, detail.pk)

    def test_reviewer_approval_links_same_record(self):
        detail = self.detail(status='pending')
        self.client.force_login(self.owner)
        with patch('event.notifications.notify_applicant_of_result'):
            response = self.client.post(reverse('event:lt_application_review', args=[detail.pk]), {
                'action': 'approve', 'event': self.event.pk, 'start_time': '22:15', 'duration': '30',
            })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(VketPresentation.objects.filter(published_event_detail=detail).exists())

    def test_both_edit_directions_preserve_owner_body_and_url(self):
        detail = self.detail()
        link_approved_application(detail)
        detail.theme = '本人編集'
        detail.save(update_fields=['theme'])
        presentation = VketPresentation.objects.get()
        self.assertEqual(presentation.theme, '本人編集')
        presentation.theme = '主催者編集'
        presentation.requested_start_time = time(22, 45)
        presentation.save()
        self.participation.refresh_from_db()
        confirm_participation_schedule(self.participation, use_requested=True)
        detail.refresh_from_db()
        self.assertEqual(detail.theme, '主催者編集')
        self.assertEqual(detail.start_time, time(22, 45))
        self.assertEqual(detail.applicant_id, self.speaker.pk)
        self.assertEqual(detail.contents, '残す本文')

    def test_deleted_normal_detail_does_not_revive(self):
        detail = self.detail()
        link_approved_application(detail)
        detail.delete()
        self.participation.refresh_from_db()
        confirm_participation_schedule(self.participation)
        self.assertIsNotNone(EventDetail.all_objects.get(pk=detail.pk).deleted_at)
        self.assertEqual(EventDetail.all_objects.count(), 1)

    def test_vket_removal_does_not_delete_or_recreate_normal_talk(self):
        detail = self.detail(time(22, 45))
        link_approved_application(detail)
        delete_requested_presentation(VketPresentation.objects.get())
        self.participation.refresh_from_db()
        self.participation.requested_duration = self.participation.confirmed_duration = 60
        self.participation.save()
        confirm_participation_schedule(self.participation)
        detail.refresh_from_db()
        self.event.refresh_from_db()
        self.assertIsNone(detail.deleted_at)
        self.assertEqual(self.event.duration, 75)
        self.assertFalse(VketPresentation.objects.exists())

    def test_participation_withdrawal_preserves_normal_talk(self):
        detail = self.detail()
        link_approved_application(detail)
        clear_participation_publication(self.participation)
        detail.refresh_from_db()
        self.assertIsNone(detail.deleted_at)
        self.assertEqual(VketPresentation.objects.get().published_event_detail_id, detail.pk)

    def test_duplicates_warn_without_merging(self):
        first, second = self.detail(), self.detail()
        self.assertTrue(application_warnings(second))
        link_approved_application(first)
        link_approved_application(second)
        self.assertEqual(VketPresentation.objects.count(), 2)

    def test_fixed_event_form_rejects_other_event(self):
        other = make_event(self.community)
        form = LTApplicationForm(community=self.community, user=self.speaker, fixed_event_pk=self.event.pk)
        self.assertEqual(list(form.fields['event'].queryset.values_list('pk', flat=True)), [self.event.pk])
        self.assertNotIn(other.pk, form.fields['event'].queryset.values_list('pk', flat=True))

    def test_night_overrun_extends_into_next_day(self):
        self.event.start_time = time(23, 30)
        self.event.save()
        self.participation.requested_start_time = self.participation.confirmed_start_time = time(23, 30)
        self.participation.save()
        detail = self.detail(time(0, 45))
        link_approved_application(detail)
        self.event.refresh_from_db()
        self.assertEqual(self.event.duration, 105)

    def test_stale_participation_does_not_overwrite_applicant(self):
        # 承認前のFKキャッシュを意図的に保持する。
        _ = self.participation.collaboration
        detail = self.detail()
        link_approved_application(detail)
        confirm_participation_schedule(self.participation)
        detail.refresh_from_db()
        self.assertEqual(detail.applicant_id, self.speaker.pk)

    def test_edit_after_vket_removal_still_extends_event(self):
        detail = self.detail(time(22, 45))
        link_approved_application(detail)
        presentation = VketPresentation.objects.get()
        delete_requested_presentation(presentation)
        detail.duration = 60
        detail.save(update_fields=['duration'])
        self.event.refresh_from_db()
        self.participation.refresh_from_db()
        self.assertEqual(self.event.duration, 105)
        self.assertEqual(self.participation.confirmed_duration, 105)

    def test_malformed_source_markers_are_ignored(self):
        self.collaboration.settings_json = {'normal_application_links': {'²': 123, 'bad': 1, '2': True, '3': '4'}}
        self.collaboration.save()
        self.assertEqual(application_links(self.collaboration), {})

    def test_extended_duration_remains_valid_in_both_forms(self):
        from vket.forms import VketApplyForm, VketApplyPermissions, VketManageParticipationForm
        link_approved_application(self.detail(time(22, 45)))
        self.participation.refresh_from_db()
        form = VketApplyForm(collaboration=self.collaboration, community=self.community,
            participation=self.participation, permissions=VketApplyPermissions(can_edit_schedule=False, can_edit_lt=True))
        self.assertEqual(form.fields['requested_duration'].clean(75), 75)
        form = VketManageParticipationForm(collaboration=self.collaboration, participation=self.participation)
        self.assertEqual(form.fields['confirmed_duration'].clean(75), 75)

    def test_title_separator_variants_warn_without_merging(self):
        first = self.detail()
        second = self.detail()
        first.theme = '合成演題－脳'
        first.save(update_fields=['theme'])
        second.theme = '合成演題 脳'
        second.save(update_fields=['theme'])
        self.assertIn('同じ開催回', application_warnings(second)[0])

    def test_vket_duplicate_input_warns_and_rows_are_retained(self):
        from vket.application_link import vket_input_warnings
        link_approved_application(self.detail())
        link_approved_application(self.detail())
        self.participation.refresh_from_db()
        self.assertEqual(len(vket_input_warnings(self.participation)), 2)
        self.assertEqual(self.participation.presentations.count(), 2)

    def test_approval_before_vket_confirmation_links_and_survives_daily_sync(self):
        self.participation.published_event = None
        self.participation.confirmed_date = None
        self.participation.confirmed_start_time = None
        self.participation.confirmed_duration = None
        self.participation.progress = 'applied'
        self.participation.save()
        detail = self.detail(time(22, 45))
        link_approved_application(detail)
        auto_confirm_schedules()
        detail.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(VketPresentation.objects.get().published_event_detail_id, detail.pk)
        self.assertEqual(detail.applicant_id, self.speaker.pk)
        self.assertEqual(self.event.duration, 75)
        self.assertEqual(EventDetail.objects.count(), 1)

    def test_midnight_talks_warn_using_event_day(self):
        first = self.detail(time(23, 50))
        second = self.detail(time(0, 10))
        self.assertTrue(any('時間が重な' in warning for warning in application_warnings(second)))
        self.assertTrue(any('時間が重な' in warning for warning in application_warnings(first)))
