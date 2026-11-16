"""登録経路を問わない取り込み、重複回避、削除と既存データの取り込み。"""
from datetime import time, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from event.models import EventDetail
from tests.factories import make_community, make_event, make_event_detail
from user_account.models import APIKey
from vket.application_link import link_approved_application, is_normal_presentation
from vket.models import VketCollaboration, VketParticipation, VketPresentation
from vket.schedule import blocks_from
from vket.services import sync_participation_publication, confirm_participation_schedule, delete_requested_presentation
from vket.views.apply import ApplyView
from vket.forms import VketApplyPermissions
from ._vket_test_bases import VketApplyFlowBase


class AnyEntryLinkTests(VketApplyFlowBase):
    def setUp(self):
        super().setUp()
        self.event = self.community.events.get()
        self.participation = VketParticipation.objects.create(
            collaboration=self.collaboration, community=self.community, published_event=self.event,
            requested_date=self.event.date, confirmed_date=self.event.date,
            requested_start_time=time(22), confirmed_start_time=time(22),
            requested_duration=60, confirmed_duration=60, applied_by=self.owner, progress='rehearsal',
        )
        self.client.force_login(self.owner)
        self._set_active_community()

    def detail(self, **kwargs):
        values = dict(theme='合成演題', speaker='合成発表者', status='approved',
                      start_time=time(22, 15), duration=30, article_consent='ng')
        values.update(kwargs)
        return make_event_detail(self.event, **values)

    def presentation(self, **kwargs):
        values = dict(speaker='合成発表者', theme='合成演題', requested_start_time=time(22, 15),
                      confirmed_start_time=time(22, 15), duration=30, status='confirmed')
        values.update(kwargs)
        return VketPresentation.objects.create(participation=self.participation, **values)

    def assert_linked(self, detail):
        presentation = VketPresentation.objects.get(published_event_detail=detail)
        self.assertEqual(presentation.participation_id, self.participation.pk)
        self.assertEqual((presentation.speaker, presentation.theme), (detail.speaker, detail.theme))
        self.assertEqual((presentation.requested_start_time, presentation.confirmed_start_time),
                         (detail.start_time, detail.start_time))
        self.assertEqual(presentation.duration, detail.duration)
        self.assertTrue(is_normal_presentation(presentation))
        return presentation

    def test_direct_create_view_imports_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('event:detail_create', args=[self.event.pk]), {
                'detail_type': 'LT', 'speaker': '合成発表者', 'theme': '合成演題',
                'start_time': '22:15', 'duration': 30, 'article_consent': 'ng',
                'recording_policy': 'forbidden',
            })
            self.assertFalse(VketPresentation.objects.exists())
        self.assertEqual(response.status_code, 302)
        self.assert_linked(EventDetail.objects.get(event=self.event))

    def test_api_create_and_update_import_and_mirror(self):
        _, key = APIKey.create_with_raw_key(user=self.owner, name='合成 API')
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {key}')
        with self.captureOnCommitCallbacks(execute=True):
            response = client.post(reverse('event-detail-api-list'), {
                'event': self.event.pk, 'detail_type': 'LT', 'speaker': '合成発表者',
                'theme': '合成演題', 'start_time': '22:15:00', 'duration': 30,
            }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        detail = EventDetail.objects.get(pk=response.data['id'])
        presentation = self.assert_linked(detail)
        with self.captureOnCommitCallbacks(execute=True):
            response = client.patch(reverse('event-detail-api-detail', args=[detail.pk]),
                                    {'theme': '合成変更演題'}, format='json')
        self.assertEqual(response.status_code, 200)
        presentation.refresh_from_db()
        self.assertEqual(presentation.theme, '合成変更演題')
        self.assertEqual(VketPresentation.objects.count(), 1)

    def test_pending_to_approved_imports(self):
        with self.captureOnCommitCallbacks(execute=True):
            detail = self.detail(status='pending')
        self.assertFalse(VketPresentation.objects.exists())
        with self.captureOnCommitCallbacks(execute=True):
            detail.status = 'approved'
            detail.save(update_fields=['status'])
        self.assert_linked(detail)

    def test_move_to_vket_event_imports(self):
        other = make_event(self.community, event_date=self.event.date + timedelta(days=14))
        with self.captureOnCommitCallbacks(execute=True):
            detail = make_event_detail(other, theme='移動する合成演題', speaker='合成発表者', status='approved')
        self.assertFalse(VketPresentation.objects.exists())
        with self.captureOnCommitCallbacks(execute=True):
            detail.event = self.event
            detail.save(update_fields=['event'])
        self.assert_linked(detail)

    def test_existing_request_is_reused_with_normalized_names(self):
        presentation = self.presentation(speaker='合成 発表者', theme='合成－演題', status='draft')
        with self.captureOnCommitCallbacks(execute=True):
            detail = self.detail()
            detail.save()
        self.assertEqual(self.assert_linked(detail).pk, presentation.pk)
        self.assertEqual(VketPresentation.objects.count(), 1)

    def test_request_with_deleted_detail_is_reused(self):
        deleted = self.detail()
        deleted.delete()
        presentation = self.presentation(published_event_detail=deleted)
        with self.captureOnCommitCallbacks(execute=True):
            detail = self.detail()
        self.assertEqual(self.assert_linked(detail).pk, presentation.pk)
        self.assertIsNotNone(EventDetail.all_objects.get(pk=deleted.pk).deleted_at)

    def test_request_with_live_detail_is_not_reused(self):
        first = self.detail()
        self.presentation(published_event_detail=first)
        with self.captureOnCommitCallbacks(execute=True):
            second = self.detail()
        self.assert_linked(second)
        self.assertEqual(VketPresentation.objects.count(), 2)

    def test_sync_links_existing_detail_without_duplicate_and_preserves_owner(self):
        detail = self.detail(speaker='合成 発表者', theme='合成－演題',
                             applicant=self.other_user, contents='保持する記事')
        presentation = self.presentation()
        with self.captureOnCommitCallbacks(execute=True):
            sync_participation_publication(self.participation)
        detail.refresh_from_db()
        self.assertEqual(self.assert_linked(detail).pk, presentation.pk)
        self.assertEqual(EventDetail.all_objects.count(), 1)
        self.assertEqual((detail.applicant_id, detail.contents), (self.other_user.pk, '保持する記事'))

    def test_sync_does_not_use_detail_linked_to_another_presentation(self):
        detail = self.detail()
        first = self.presentation(published_event_detail=detail)
        second = self.presentation()
        with self.captureOnCommitCallbacks(execute=True):
            sync_participation_publication(self.participation)
        second.refresh_from_db()
        self.assertNotEqual(second.published_event_detail_id, first.published_event_detail_id)
        self.assertEqual(EventDetail.objects.count(), 2)
        self.assertEqual(VketPresentation.objects.count(), 2)

    def test_sync_new_detail_does_not_become_normal_import(self):
        presentation = self.presentation()
        with self.captureOnCommitCallbacks(execute=True), patch('vket.activity.notify_activity') as notify:
            sync_participation_publication(self.participation)
        presentation.refresh_from_db()
        self.assertIsNotNone(presentation.published_event_detail_id)
        self.assertFalse(is_normal_presentation(presentation))
        self.assertEqual(VketPresentation.objects.count(), 1)
        notify.assert_not_called()

    def test_ineligible_details_are_not_imported(self):
        for kwargs in ({'status': 'pending'}, {'status': 'rejected'}, {'detail_type': 'SPECIAL'}):
            with self.subTest(kwargs=kwargs), self.captureOnCommitCallbacks(execute=True):
                self.detail(**kwargs)
        with self.captureOnCommitCallbacks(execute=True):
            detail = self.detail()
            detail.delete()
        self.assertFalse(VketPresentation.objects.exists())

    def test_raw_and_publication_sync_saves_are_not_imported(self):
        from event.signals import import_approved_detail_to_vket
        detail = self.detail()
        with self.captureOnCommitCallbacks(execute=True):
            import_approved_detail_to_vket(EventDetail, detail, raw=True)
            detail._vket_publication_sync = True
            detail.save()
        self.assertFalse(VketPresentation.objects.exists())

    def test_rollback_never_imports(self):
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(ValueError), transaction.atomic():
                self.detail()
                raise ValueError('合成ロールバック')
        self.assertFalse(VketPresentation.objects.exists())

    def test_after_deadline_and_locked_phase_still_imports(self):
        self.collaboration.registration_deadline = self.event.date - timedelta(days=1)
        self.collaboration.lt_deadline = self.event.date - timedelta(days=1)
        self.collaboration.phase = VketCollaboration.Phase.LOCKED
        self.collaboration.save()
        with self.captureOnCommitCallbacks(execute=True):
            detail = self.detail()
        self.assert_linked(detail)

    def test_confirmed_schedule_fallback_imports_without_published_event(self):
        self.participation.published_event = None
        self.participation.save()
        with self.captureOnCommitCallbacks(execute=True):
            detail = self.detail()
        self.assert_linked(detail)

    def test_import_notifies_once_only_after_commit(self):
        self.collaboration.settings_json = {'activity_webhook_url': 'https://discord.com/api/webhooks/123/synthetic'}
        self.collaboration.save()
        with patch('vket.activity.post_discord_webhook') as send:
            with self.captureOnCommitCallbacks(execute=True):
                detail = self.detail()
                detail.save()
                send.assert_not_called()
            send.assert_called_once()
            self.assertIn('発表の追加', send.call_args.args[1]['content'])
            link_approved_application(detail)
            self.assertEqual(send.call_count, 1)

    def test_reused_request_does_not_notify_as_added(self):
        self.presentation()
        with patch('vket.activity.notify_activity') as notify, self.captureOnCommitCallbacks(execute=True):
            self.detail()
        notify.assert_not_called()

    def test_vket_organizer_delete_soft_deletes_linked_detail(self):
        with self.captureOnCommitCallbacks(execute=True):
            detail = self.detail()
        presentation = self.assert_linked(detail)
        response = self.client.post(reverse('vket:presentation_delete', args=[self.collaboration.pk, presentation.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(EventDetail.all_objects.get(pk=detail.pk).deleted_at)
        self.assertFalse(VketPresentation.objects.exists())
        confirm_participation_schedule(self.participation)
        self.assertFalse(EventDetail.objects.exists())

    def test_vket_admin_delete_soft_deletes_linked_detail(self):
        self.owner.is_superuser = True
        self.owner.save()
        with self.captureOnCommitCallbacks(execute=True):
            detail = self.detail()
        presentation = self.assert_linked(detail)
        response = self.client.post(reverse('vket:manage_presentation_delete', args=[self.collaboration.pk, presentation.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(EventDetail.all_objects.get(pk=detail.pk).deleted_at)
        self.assertFalse(VketPresentation.objects.exists())

    def test_delete_confirmation_explains_public_page_only_for_linked(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.detail()
        self.assertContains(self.client.get(reverse('vket:status', args=[self.collaboration.pk])), '公開ページからも消えます')
        self.owner.is_superuser = True
        self.owner.save()
        self.assertContains(self.client.get(reverse('vket:manage', args=[self.collaboration.pk])), '公開ページからも消えます')
        VketPresentation.objects.all().delete()
        self.presentation()
        self.assertNotContains(self.client.get(reverse('vket:status', args=[self.collaboration.pk])), '公開ページからも消えます')
        self.assertNotContains(self.client.get(reverse('vket:manage', args=[self.collaboration.pk])), '公開ページからも消えます')

    def test_deleted_public_detail_is_hidden_in_status_manage_apply_and_timetable(self):
        self.owner.is_superuser = True
        self.owner.save()
        with self.captureOnCommitCallbacks(execute=True):
            detail = self.detail(theme='非表示になる合成演題')
        response = self.client.post(reverse('event:detail_delete', args=[detail.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(EventDetail.all_objects.get(pk=detail.pk).deleted_at)
        for route in ['vket:status', 'vket:manage', 'vket:apply']:
            with self.subTest(route=route):
                self.assertNotContains(self.client.get(reverse(route, args=[self.collaboration.pk])), detail.theme)
        self.assertEqual(blocks_from([self.participation]), [])

    def test_apply_saves_visible_row_without_overwriting_hidden_row(self):
        deleted = self.detail(theme='削除した合成演題')
        self.presentation(theme=deleted.theme, published_event_detail=deleted, order=0)
        deleted.delete()
        visible = self.presentation(theme='表示する合成演題', order=1)
        view = ApplyView()
        formset = view._build_formset(collaboration=self.collaboration, participation=self.participation,
            permissions=VketApplyPermissions(can_edit_schedule=True, can_edit_lt=True), user=self.owner)
        self.assertEqual(formset.initial[0]['theme'], visible.theme)
        self.assertEqual(formset.initial[0]['presentation_id'], visible.pk)
        view._save_presentations(self.participation, [{'presentation_id': visible.pk, 'speaker': '合成発表者',
                                                      'theme': '更新した合成演題', 'lt_start_time': time(22, 30)}])
        visible.refresh_from_db()
        self.assertEqual(visible.theme, '更新した合成演題')
        self.assertEqual(VketPresentation.objects.get(published_event_detail=deleted).theme, deleted.theme)

    def test_backfill_dry_run_writes_nothing_and_apply_imports_without_notice(self):
        detail = self.detail()
        self.detail(status='pending')
        presentation = self.presentation()
        before = self.collaboration.settings_json
        out = StringIO()
        with CaptureQueriesContext(connection) as queries:
            call_command('link_unlinked_vket_details', stdout=out)
        self.assertTrue(all(query['sql'].lstrip().upper().startswith('SELECT') for query in queries))
        self.collaboration.refresh_from_db()
        presentation.refresh_from_db()
        self.assertEqual(self.collaboration.settings_json, before)
        self.assertIsNone(presentation.published_event_detail_id)
        self.assertEqual(VketPresentation.objects.count(), 1)
        self.assertIn(f'detail={detail.pk} status=approved start=22:15 theme=合成演題 presentation={presentation.pk}', out.getvalue())
        self.assertIn('dry-run: candidates=1 linked=0', out.getvalue())
        out = StringIO()
        with patch('vket.activity.notify_activity') as notify:
            call_command('link_unlinked_vket_details', apply=True, stdout=out)
        notify.assert_not_called()
        self.assertEqual(self.assert_linked(detail).pk, presentation.pk)
        self.assertIn('apply: candidates=1 linked=1', out.getvalue())
        out = StringIO()
        call_command('link_unlinked_vket_details', apply=True, stdout=out)
        self.assertIn('candidates=0 linked=0', out.getvalue())

    def test_backfill_collaboration_filter(self):
        detail = self.detail()
        out = StringIO()
        call_command('link_unlinked_vket_details', collaboration=self.collaboration.pk + 100, apply=True, stdout=out)
        self.assertFalse(VketPresentation.objects.exists())
        call_command('link_unlinked_vket_details', collaboration=self.collaboration.pk, apply=True, stdout=out)
        self.assert_linked(detail)

    def test_ambiguous_match_does_not_import(self):
        other = VketCollaboration.objects.create(
            slug='synthetic-other', name='合成コラボ', period_start=self.event.date,
            period_end=self.event.date, registration_deadline=self.event.date, lt_deadline=self.event.date,
        )
        VketParticipation.objects.create(collaboration=other, community=self.community,
                                         published_event=self.event, progress='applied')
        with self.assertLogs('vket.application_link', level='WARNING') as logs, self.captureOnCommitCallbacks(execute=True):
            self.detail()
        self.assertIn('application_vket_link_ambiguous', logs.output[0])
        self.assertFalse(VketPresentation.objects.exists())

    def test_sync_excludes_pending_deleted_and_non_presentation_details(self):
        pending = self.detail(status='pending')
        special = self.detail(detail_type='SPECIAL')
        deleted = self.detail()
        deleted.delete()
        presentation = self.presentation()
        with self.captureOnCommitCallbacks(execute=True):
            sync_participation_publication(self.participation)
        presentation.refresh_from_db()
        self.assertNotIn(presentation.published_event_detail_id, [pending.pk, special.pk, deleted.pk])
        self.assertEqual(EventDetail.all_objects.count(), 4)
        self.assertEqual(VketPresentation.objects.count(), 1)

    def test_not_applied_or_withdrawn_participation_is_not_imported(self):
        for fields in [{'progress': 'not_applied'}, {'progress': 'applied', 'lifecycle': 'withdrawn'}]:
            with self.subTest(fields=fields):
                VketParticipation.objects.filter(pk=self.participation.pk).update(**fields)
                with self.captureOnCommitCallbacks(execute=True):
                    self.detail()
                self.assertFalse(VketPresentation.objects.exists())

    def test_backfill_ambiguous_candidate_is_reported_but_not_applied(self):
        other = VketCollaboration.objects.create(
            slug='synthetic-ambiguous', name='合成コラボ', period_start=self.event.date,
            period_end=self.event.date, registration_deadline=self.event.date, lt_deadline=self.event.date,
        )
        VketParticipation.objects.create(collaboration=other, community=self.community,
                                         published_event=self.event, progress='applied')
        self.detail()
        out = StringIO()
        call_command('link_unlinked_vket_details', collaboration=self.collaboration.pk, apply=True, stdout=out)
        self.assertFalse(VketPresentation.objects.exists())
        self.assertIn('status=ambiguous', out.getvalue())
        self.assertIn('candidates=1 linked=0 ambiguous=1', out.getvalue())

    def test_import_does_not_lock_vket_until_save_transaction_commits(self):
        from vket.application_link import lock_application_event
        with patch('vket.application_link.lock_application_event', wraps=lock_application_event) as lock:
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    detail = self.detail()
                    lock.assert_not_called()
                lock.assert_not_called()
            lock.assert_called_once()
        self.assert_linked(detail)

    def test_sync_link_does_not_restore_processed_pending_deletions(self):
        old = self.detail(theme='取り下げる合成演題')
        removed = self.presentation(theme=old.theme, published_event_detail=old)
        delete_requested_presentation(removed)
        detail = self.detail()
        presentation = self.presentation()
        sync_participation_publication(self.participation)
        self.assertEqual(self.assert_linked(detail).pk, presentation.pk)
        self.collaboration.refresh_from_db()
        self.assertNotIn('pending_presentation_deletions', self.collaboration.settings_json)
        self.assertIsNotNone(EventDetail.all_objects.get(pk=old.pk).deleted_at)

    def withdrawn_detail(self):
        detail = self.detail()
        VketPresentation.objects.all().delete()
        removed = self.presentation(published_event_detail=detail)
        delete_requested_presentation(removed)
        return detail

    def test_pending_deletion_is_not_reimported_on_save(self):
        detail = self.withdrawn_detail()
        with self.captureOnCommitCallbacks(execute=True):
            detail.save()
        self.assertFalse(VketPresentation.objects.exists())
        sync_participation_publication(self.participation)
        self.assertIsNotNone(EventDetail.all_objects.get(pk=detail.pk).deleted_at)

    def test_backfill_skips_pending_deletion(self):
        detail = self.withdrawn_detail()
        out = StringIO()
        call_command('link_unlinked_vket_details', apply=True, stdout=out)
        self.assertFalse(VketPresentation.objects.exists())
        self.assertNotIn(f'detail={detail.pk}', out.getvalue())

    def test_signal_import_failure_is_logged_not_raised(self):
        with patch('vket.application_link.link_approved_application', side_effect=RuntimeError('合成失敗')):
            with self.assertLogs('vket.application_link', level='ERROR') as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    detail = self.detail()
        self.assertIn('event_detail_vket_import_failed', logs.output[0])
        self.assertEqual(logs.records[0].event_detail_id, detail.pk)
        self.assertEqual(logs.records[0].event_id, self.event.pk)
        self.assertTrue(EventDetail.objects.filter(pk=detail.pk).exists())

    def test_delete_skips_detail_moved_to_other_community(self):
        with self.captureOnCommitCallbacks(execute=True):
            detail = self.detail()
        presentation = self.assert_linked(detail)
        other = make_community(name='合成の別集会', owner=self.other_user, status='approved')
        moved_to = make_event(other, event_date=self.event.date, start_time=time(22), duration=60)
        EventDetail.objects.filter(pk=detail.pk).update(event=moved_to)
        with self.assertLogs('vket.services', level='WARNING') as logs:
            response = self.client.post(
                reverse('vket:presentation_delete', args=[self.collaboration.pk, presentation.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn('vket_linked_detail_delete_skipped', logs.output[0])
        self.assertIsNone(EventDetail.objects.get(pk=detail.pk).deleted_at)
        self.assertFalse(VketPresentation.objects.exists())

    def test_delete_skips_detail_moved_to_other_event_of_same_community(self):
        with self.captureOnCommitCallbacks(execute=True):
            detail = self.detail()
        presentation = self.assert_linked(detail)
        moved_to = make_event(self.community, event_date=self.collaboration.period_end + timedelta(days=30),
                              start_time=time(22), duration=60)
        EventDetail.objects.filter(pk=detail.pk).update(event=moved_to)
        delete_requested_presentation(VketPresentation.objects.get(pk=presentation.pk))
        self.assertIsNone(EventDetail.objects.get(pk=detail.pk).deleted_at)

    def test_link_respects_collaboration_id(self):
        detail = self.detail()
        self.assertIsNone(link_approved_application(detail.pk, collaboration_id=self.collaboration.pk + 100))
        self.assertFalse(VketPresentation.objects.exists())
        self.assertIsNotNone(link_approved_application(detail.pk, collaboration_id=self.collaboration.pk))

    def test_backfill_escapes_control_characters(self):
        self.detail(theme='合成\x1b[31m赤\x07‮演題')
        out = StringIO()
        call_command('link_unlinked_vket_details', stdout=out)
        text = out.getvalue()
        self.assertIn('theme=合成\\x1b[31m赤\\x07\\u202e演題', text)
        self.assertNotIn('\x1b', text)
        self.assertNotIn('‮', text)
    def apply_post(self, rows):
        data = {'requested_date': self.event.date.isoformat(), 'requested_start_time': '22:00',
                'requested_duration': '60', 'lt_slot_minutes': '30', 'organizer_note': ''}
        data.update(self._make_formset_data(rows, initial_forms=len(rows)))
        return self.client.post(reverse('vket:apply', args=[self.collaboration.pk]), data, follow=True)

    def test_apply_stale_delete_row_does_not_delete_other_presentation(self):
        first = self.detail(theme='合成演題A')
        a = self.presentation(theme=first.theme, published_event_detail=first, order=0)
        b = self.presentation(theme='合成演題B', order=1)
        first.delete()  # 別の画面で A の公開詳細が消えた後、古いフォームで A を消す
        with self.assertLogs('vket.views.apply', level='WARNING') as logs:
            response = self.apply_post([
                {'presentation_id': a.pk, 'speaker': a.speaker, 'theme': a.theme, 'DELETE': True},
                {'presentation_id': b.pk, 'speaker': b.speaker, 'theme': b.theme},
            ])
        self.assertIn('vket_apply_stale_presentation_row', logs.output[0])
        self.assertTrue(VketPresentation.objects.filter(pk=b.pk).exists())
        self.assertTrue(VketPresentation.objects.filter(pk=a.pk).exists())
        self.assertContains(response, '別の画面で変更された発表があります')

    def test_apply_ignores_presentation_id_of_other_participation(self):
        other = make_community(name='合成の別集会2', owner=self.other_user, status='approved')
        other_participation = VketParticipation.objects.create(
            collaboration=self.collaboration, community=other, progress='applied')
        target = VketPresentation.objects.create(participation=other_participation, speaker='他', theme='他の演題')
        self.apply_post([{'presentation_id': target.pk, 'speaker': '改ざん', 'theme': '改ざん', 'DELETE': True}])
        target.refresh_from_db()
        self.assertEqual(target.theme, '他の演題')
        self.apply_post([{'presentation_id': target.pk, 'speaker': '改ざん', 'theme': '改ざん'}])
        target.refresh_from_db()
        self.assertEqual(target.theme, '他の演題')

    def test_signal_failure_log_does_not_query_db(self):
        from vket.application_link import import_approved_detail
        from django.db.utils import OperationalError
        # DB 障害中でも、失敗の記録が新たな例外を出さない。
        with patch('vket.application_link.link_approved_application', side_effect=OperationalError('合成障害')), \
                patch('django.db.backends.utils.CursorWrapper.execute', side_effect=OperationalError('合成障害')), \
                self.assertLogs('vket.application_link', level='ERROR') as logs:
            import_approved_detail(10, 20)
        self.assertEqual((logs.records[0].event_detail_id, logs.records[0].event_id), (10, 20))

    def test_collaboration_id_mismatch_on_ambiguous_changes_nothing(self):
        other = VketCollaboration.objects.create(
            slug='synthetic-ambiguous-2', name='合成コラボ', period_start=self.event.date,
            period_end=self.event.date, registration_deadline=self.event.date, lt_deadline=self.event.date,
        )
        VketParticipation.objects.create(collaboration=other, community=self.community,
                                         published_event=self.event, progress='applied')
        detail = self.detail(start_time=time(23, 30))
        before = self.collaboration.settings_json
        self.assertIsNone(link_approved_application(detail.pk, collaboration_id=self.collaboration.pk))
        self.collaboration.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(self.collaboration.settings_json, before)
        self.assertEqual(self.event.duration, 60)


class NonVketEventSaveTests(VketApplyFlowBase):
    def test_direct_add_on_unrelated_event_neither_locks_nor_extends(self):
        event = make_event(self.community, event_date=self.collaboration.period_end + timedelta(days=30),
                           start_time=time(22), duration=60)
        with self.captureOnCommitCallbacks(execute=True):
            make_event_detail(event, theme='合成演題', speaker='合成発表者', status='approved',
                              start_time=time(22, 45), duration=30, article_consent='ng')
        event.refresh_from_db()
        self.assertEqual(event.duration, 60)
        self.assertFalse(VketPresentation.objects.exists())
