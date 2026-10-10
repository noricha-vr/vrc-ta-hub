"""日程の自動確定と、主催者の変更の次回反映。Webhook はモックする。"""
from datetime import time, timedelta
from unittest import mock

from django.db import transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from event.models import Event, EventDetail
from tests.factories import make_community, make_event
from vket.auto_confirm import auto_confirm_schedules
from vket.models import VketCollaboration, VketParticipation, VketPresentation
from vket.schedule import ScheduleBlock, find_conflicting_pairs
from vket.services import confirm_participation_schedule, delete_requested_presentation, PENDING_DELETIONS_KEY

from ._vket_test_bases import VketApplyFlowBase


@override_settings(REQUEST_TOKEN='test-request-token')
class VketAutoConfirmTests(VketApplyFlowBase):
    def setUp(self):
        super().setUp()
        self.url = reverse('vket:auto_confirm_run')
        self.collaboration.settings_json = {
            'activity_webhook_url': 'https://discord.com/api/webhooks/123/token',
        }
        self.collaboration.save()
        self.send_patch = mock.patch('vket.activity.post_discord_webhook')
        self.send = self.send_patch.start()
        self.addCleanup(self.send_patch.stop)
        self.client.force_login(self.owner)
        self._set_active_community()

    def _participation(self, community=None, start=time(21), *, day=None, applied_at=None):
        return VketParticipation.objects.create(
            collaboration=self.collaboration, community=community or self.community,
            requested_date=day or self.collaboration.period_start,
            requested_start_time=start, requested_duration=60,
            applied_at=applied_at or timezone.now(), applied_by=self.owner,
            progress=VketParticipation.Progress.APPLIED,
        )

    def _presentation(self, participation, start=time(21), **kwargs):
        return VketPresentation.objects.create(
            participation=participation, speaker='登壇者', theme='テーマ',
            requested_start_time=start, duration=30, **kwargs,
        )

    def _run(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.get(self.url, HTTP_REQUEST_TOKEN='test-request-token')
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _apply(self, participation, rows, *, start='22:00', day=None, duration='90', slot_minutes='20'):
        data = {
            'requested_date': (day or participation.requested_date).isoformat(),
            'requested_start_time': start, 'requested_duration': duration,
            'lt_slot_minutes': slot_minutes, 'organizer_note': participation.organizer_note,
        }
        data.update(self._make_formset_data(rows, initial_forms=participation.presentations.count()))
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}), data)
        self.assertEqual(response.status_code, 302)

    def test_organizer_can_edit_schedule_and_published_presentation_after_deadlines_and_notify(self):
        """完了条件1: 確定・締切後も希望だけを編集でき、既存の変更通知が一回届く。"""
        participation = self._participation()
        presentation = self._presentation(participation)
        self._run()
        self.send.reset_mock()
        self.collaboration.phase = VketCollaboration.Phase.ANNOUNCEMENT
        self.collaboration.registration_deadline = timezone.localdate() - timedelta(days=2)
        self.collaboration.lt_deadline = timezone.localdate() - timedelta(days=1)
        self.collaboration.save()
        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))
        self.assertContains(response, '変更は次の自動確定で反映されます')
        self.assertFalse(response.context['form'].fields['requested_start_time'].disabled)
        self.assertFalse(response.context['formset'].forms[0].fields['lt_start_time'].disabled)
        self._apply(participation, [{'speaker': '変更した登壇者', 'theme': '変更したテーマ', 'lt_start_time': '22:10'}])
        self.send.assert_called_once()
        participation.refresh_from_db()
        presentation.refresh_from_db()
        self.assertEqual(participation.requested_start_time, time(22))
        self.assertEqual(participation.confirmed_start_time, time(21))
        self.assertEqual(participation.confirmed_duration, 60)
        self.assertEqual(presentation.requested_start_time, time(22, 10))
        self.assertEqual(presentation.confirmed_start_time, time(21))
        self.assertEqual(presentation.published_event_detail.speaker, '登壇者')
        self.assertEqual(presentation.published_event_detail.start_time, time(21))

    def test_nonconflicting_participations_publish_and_earlier_application_wins_conflict(self):
        """完了条件2: 申込み順に発表の重なりを判定し、重ならない参加も公開する。"""
        # pk は後でも、applied_at が先の参加が優先される。
        late = self._participation(applied_at=timezone.now())
        self._presentation(late)
        early = self._participation(make_community(name='先の集会'), applied_at=timezone.now() - timedelta(days=1))
        self._presentation(early)
        separate = self._participation(make_community(name='別の集会'))
        # 参加枠は同じでも発表が重ならなければ確定できる。
        self._presentation(separate, time(22))
        with mock.patch('vket.services.clear_index_view_cache') as clear_cache:
            result = self._run()
        self.assertEqual(result, {'confirmed': 2, 'skipped': 1, 'incomplete': 0})
        self.assertEqual(clear_cache.call_count, 2)
        for participation in (early, separate):
            participation.refresh_from_db()
            self.assertEqual(participation.confirmed_date, participation.requested_date)
            self.assertEqual(participation.confirmed_start_time, participation.requested_start_time)
            self.assertEqual(participation.progress, VketParticipation.Progress.REHEARSAL)
            self.assertIsNotNone(participation.published_event_id)
            presentation = participation.presentations.get()
            self.assertEqual(presentation.status, VketPresentation.Status.CONFIRMED)
            self.assertEqual(presentation.confirmed_start_time, presentation.requested_start_time)
            self.assertEqual(presentation.published_event_detail.start_time, presentation.requested_start_time)
        late.refresh_from_db()
        self.assertIsNone(late.confirmed_date)
        self.assertIsNone(late.published_event_id)

    def test_next_confirmation_updates_schedule_text_times_additions_and_deletions(self):
        """完了条件3: 変更・追加・公開発表の削除を、次の確定でまとめて公開に反映する。"""
        participation = self._participation()
        presentation = self._presentation(participation)
        removed = self._presentation(participation, time(21, 30), order=1)
        self._run()
        presentation.refresh_from_db()
        removed.refresh_from_db()
        removed_id = removed.published_event_detail_id
        kept_id = presentation.published_event_detail_id
        tomorrow = participation.requested_date + timedelta(days=1)
        make_event(self.community, event_date=tomorrow)
        self._apply(participation, [
            {'speaker': '新登壇者', 'theme': '新テーマ', 'lt_start_time': '22:10'},
            {'DELETE': True},
            {'speaker': '追加登壇者', 'theme': '追加テーマ', 'lt_start_time': '22:30'},
        ], day=tomorrow)
        self.assertFalse(VketPresentation.objects.filter(pk=removed.pk).exists())
        self.assertTrue(EventDetail.objects.filter(pk=removed_id).exists())
        participation.refresh_from_db()
        self.assertEqual(participation.confirmed_date, self.collaboration.period_start)
        self.assertEqual(participation.published_event.start_time, time(21))
        result = self._run()
        self.assertEqual(result['confirmed'], 1)
        participation.refresh_from_db()
        self.assertEqual(participation.confirmed_date, tomorrow)
        self.assertEqual(participation.confirmed_start_time, time(22))
        self.assertEqual(participation.confirmed_duration, 90)
        self.assertEqual(participation.published_event.date, tomorrow)
        self.assertEqual(participation.published_event.start_time, time(22))
        self.assertEqual(participation.published_event.duration, 90)
        detail = EventDetail.objects.get(pk=kept_id)
        self.assertEqual((detail.speaker, detail.theme, detail.start_time, detail.duration),
                         ('新登壇者', '新テーマ', time(22, 10), 20))
        self.assertFalse(EventDetail.objects.filter(pk=removed_id).exists())
        self.assertIsNotNone(EventDetail.all_objects.get(pk=removed_id).deleted_at)
        added = participation.presentations.get(speaker='追加登壇者')
        self.assertEqual(added.confirmed_start_time, time(22, 30))
        self.assertEqual(added.published_event_detail.theme, '追加テーマ')
        self.collaboration.refresh_from_db()
        self.assertNotIn(PENDING_DELETIONS_KEY, self.collaboration.settings_json)
        self.assertIn('activity_webhook_url', self.collaboration.settings_json)
        self.assertEqual(self._run()['confirmed'], 0)

    def test_summary_sent_once_with_confirmed_and_skipped_and_empty_day_sends_nothing(self):
        """完了条件4: コラボ単位で結果を一回通知し、変更のない日は送信しない。"""
        own = self._participation()
        self._presentation(own)
        other = self._participation(make_community(name='競合集会'))
        self._presentation(other)
        self._run()
        self.send.assert_called_once()
        payload = self.send.call_args.args[1]
        self.assertEqual(payload['allowed_mentions'], {'parse': []})
        self.assertEqual(len(payload['embeds']), 2)
        self.assertIn('個人開発集会', payload['embeds'][0]['description'])
        self.assertIn('競合集会', payload['embeds'][1]['description'])
        self.assertIn('個人開発集会', payload['embeds'][1]['description'])
        # 重なりの見送りが残る日は通知する。変更も見送りもない日は通知しない。
        other.lifecycle = VketParticipation.Lifecycle.WITHDRAWN
        other.save()
        self.send.reset_mock()
        self.assertEqual(self._run()['confirmed'], 0)
        self.send.assert_not_called()

    def test_token_missing_wrong_or_unconfigured_returns_403_without_processing(self):
        own = self._participation()
        self._presentation(own)
        for token in (None, '', 'wrong'):
            headers = {} if token is None else {'HTTP_REQUEST_TOKEN': token}
            self.assertEqual(self.client.get(self.url, **headers).status_code, 403)
        with override_settings(REQUEST_TOKEN=''):
            self.assertEqual(self.client.get(self.url, HTTP_REQUEST_TOKEN='test-request-token').status_code, 403)
        self.assertEqual(self.client.post(self.url, HTTP_REQUEST_TOKEN='test-request-token').status_code, 405)
        own.refresh_from_db()
        self.assertIsNone(own.confirmed_date)
        self.send.assert_not_called()

    def test_one_confirmation_per_participation_and_second_run_is_noop(self):
        own = self._participation()
        self._presentation(own)
        self._presentation(own, time(21, 30), order=1)
        with mock.patch('vket.auto_confirm.confirm_participation_schedule', wraps=confirm_participation_schedule) as confirm:
            self.assertEqual(self._run()['confirmed'], 1)
            self.assertEqual(confirm.call_count, 1)
            own.refresh_from_db()
            confirmed_at = own.schedule_confirmed_at
            detail_ids = list(own.presentations.values_list('published_event_detail_id', flat=True))
            self.send.reset_mock()
            self.assertEqual(self._run(), {'confirmed': 0, 'skipped': 0, 'incomplete': 0})
            self.assertEqual(confirm.call_count, 1)
            own.refresh_from_db()
            self.assertEqual(own.schedule_confirmed_at, confirmed_at)
            self.assertEqual(list(own.presentations.values_list('published_event_detail_id', flat=True)), detail_ids)
            self.send.assert_not_called()

    def test_unchanged_confirmed_presentations_block_changes_and_buffer_crosses_midnight(self):
        self.collaboration.settings_json['schedule_buffer_minutes'] = 10
        self.collaboration.save()
        own = self._participation(start=time(23, 40))
        self._presentation(own, time(23, 40))
        self._run()
        other = self._participation(make_community(name='翌日の集会'), start=time(0, 15),
                                    day=self.collaboration.period_start + timedelta(days=1))
        presentation = self._presentation(other, time(0, 15))
        self.assertEqual(self._run()['skipped'], 1)
        presentation.requested_start_time = time(0, 20)
        presentation.save()
        self.assertEqual(self._run()['confirmed'], 1)

    def test_changed_confirmed_schedule_is_compared_using_requested_values(self):
        own = self._participation()
        presentation = self._presentation(own)
        self._run()
        fixed = self._participation(make_community(name='固定集会'), start=time(22))
        self._presentation(fixed, time(22))
        self._run()
        presentation.refresh_from_db()
        presentation.requested_start_time = time(22, 10)
        presentation.save()
        self.assertEqual(self._run()['skipped'], 1)
        presentation.refresh_from_db()
        self.assertEqual(presentation.confirmed_start_time, time(21))
        self.assertEqual(presentation.published_event_detail.start_time, time(21))

    def test_same_applied_at_uses_pk(self):
        now = timezone.now()
        first = self._participation(applied_at=now)
        self._presentation(first)
        second = self._participation(make_community(name='同時の集会'), applied_at=now)
        self._presentation(second)
        self.assertEqual(self._run()['confirmed'], 1)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNotNone(first.confirmed_date)
        self.assertIsNone(second.confirmed_date)

    def _assert_no_public_overlap(self, participations):
        """希望値ではなく、公開イベントの発表の時間帯で検証する。"""
        event_ids = VketParticipation.objects.filter(
            pk__in=[p.pk for p in participations], published_event__isnull=False,
        ).values_list('published_event_id', flat=True)
        details = EventDetail.objects.filter(
            event_id__in=event_ids,
        ).select_related('event__community')
        blocks = [ScheduleBlock(
            participation_id=None, community_id=d.event.community_id,
            community_name=d.event.community.name, date=d.event.date,
            start=d.start_time, duration=d.duration,
        ) for d in details]
        self.assertEqual(find_conflicting_pairs(blocks), [])

    def _assert_skipped_change_keeps_old_public_slot(self, *, reverse_order=False):
        own = self._participation(applied_at=timezone.now() - timedelta(days=2))
        presentation = self._presentation(own)
        fixed = self._participation(make_community(name='固定集会'), start=time(22))
        self._presentation(fixed, time(22))
        self.assertEqual(self._run()['confirmed'], 2)
        own.refresh_from_db()
        presentation.refresh_from_db()
        presentation.requested_start_time = time(22)
        presentation.save()
        own.requested_start_time = time(22)
        own.save()
        newcomer = self._participation(
            make_community(name='旧枠を希望する集会'),
            applied_at=own.applied_at + timedelta(days=-1 if reverse_order else 1),
        )
        self._presentation(newcomer)
        self.assertEqual(self._run(), {'confirmed': 0, 'skipped': 2, 'incomplete': 0})
        own.refresh_from_db()
        presentation.refresh_from_db()
        newcomer.refresh_from_db()
        self.assertEqual(own.confirmed_start_time, time(21))
        self.assertEqual(own.published_event.start_time, time(21))
        self.assertEqual(presentation.published_event_detail.start_time, time(21))
        self.assertIsNone(newcomer.published_event_id)
        self._assert_no_public_overlap([own, fixed, newcomer])

    def test_skipped_change_keeps_old_public_slot(self):
        """A の 22 時への変更が B と重なる時、C は A の旧 21 時枠へ入れない。"""
        self._assert_skipped_change_keeps_old_public_slot()

    def test_skipped_change_keeps_old_public_slot_in_reverse_application_order(self):
        """C の申込みが A より先でも、A の旧公開枠は確保される。"""
        self._assert_skipped_change_keeps_old_public_slot(reverse_order=True)

    def test_theme_only_change_keeps_confirmed_slot_reserved(self):
        """テーマだけ変更中の参加より先に申し込んでも、公開済み枠へ入れない。"""
        own = self._participation()
        presentation = self._presentation(own)
        self._run()
        presentation.refresh_from_db()
        presentation.theme = '変更テーマ'
        presentation.save()
        newcomer = self._participation(
            make_community(name='先の申込み'), applied_at=own.applied_at - timedelta(days=1),
        )
        self._presentation(newcomer)
        self.assertEqual(self._run(), {'confirmed': 1, 'skipped': 1, 'incomplete': 0})
        own.refresh_from_db()
        newcomer.refresh_from_db()
        presentation.refresh_from_db()
        self.assertEqual(presentation.published_event_detail.theme, '変更テーマ')
        self.assertIsNone(newcomer.published_event_id)
        self._assert_no_public_overlap([own, newcomer])

    def test_public_slot_is_reserved_on_published_event_date_when_it_drifts(self):
        """公開イベントの日付が確定日とずれていても、公開中の日付の枠に他の参加を確定しない。"""
        own = self._participation()
        presentation = self._presentation(own)
        self._run()
        presentation.refresh_from_db()
        drifted_day = self.collaboration.period_start + timedelta(days=1)
        Event.objects.filter(pk=presentation.published_event_detail.event_id).update(date=drifted_day)
        newcomer = self._participation(
            make_community(name='公開日に重ねる集会'), day=drifted_day,
            applied_at=own.applied_at - timedelta(days=1),
        )
        self._presentation(newcomer)

        result = self._run()

        newcomer.refresh_from_db()
        self.assertEqual(result['skipped'], 1)
        self.assertIsNone(newcomer.published_event_id)

    def test_published_event_time_drift_is_repaired(self):
        """公開イベントの開始時刻・開催時間だけがずれた時も、次の自動確定で確定値に戻す。"""
        own = self._participation()
        self._presentation(own)
        self._run()
        own.refresh_from_db()
        Event.objects.filter(pk=own.published_event_id).update(start_time=time(23, 0), duration=15)

        self.assertEqual(self._run()['confirmed'], 1)

        event = Event.objects.get(pk=own.published_event_id)
        self.assertEqual((event.start_time, event.duration), (own.confirmed_start_time, own.confirmed_duration))

    def test_published_detail_start_time_drift_is_repaired(self):
        """公開中の発表の開始時刻だけがずれた時も、次の自動確定で確定値に戻す。"""
        own = self._participation()
        presentation = self._presentation(own)
        self._run()
        presentation.refresh_from_db()
        EventDetail.objects.filter(pk=presentation.published_event_detail_id).update(start_time=time(23, 30))

        self.assertEqual(self._run()['confirmed'], 1)

        detail = EventDetail.objects.get(pk=presentation.published_event_detail_id)
        self.assertEqual(detail.start_time, presentation.confirmed_start_time)

    def test_admin_adjusted_schedule_is_not_reverted_by_next_run(self):
        """運営が希望と違う日時で確定しても、次の自動確定で希望の日時に戻さない。"""
        own = self._participation(start=time(21))
        presentation = self._presentation(own, start=time(21))
        own.confirmed_date = own.requested_date
        own.confirmed_start_time = time(22)
        own.confirmed_duration = 60
        confirm_participation_schedule(own, presentation_times={presentation.pk: time(22)})
        own.refresh_from_db()

        self._run()

        own.refresh_from_db()
        presentation.refresh_from_db()
        self.assertEqual(own.confirmed_start_time, time(22))
        self.assertEqual(own.requested_start_time, time(22))
        event = Event.objects.get(pk=own.published_event_id)
        self.assertEqual(event.start_time, time(22))

    def test_detail_attached_to_other_event_is_reattached(self):
        """公開中の発表が同じ日付の別イベントに付いていたら、次の自動確定で公開イベントへ戻す。"""
        own = self._participation()
        presentation = self._presentation(own)
        self._run()
        own.refresh_from_db()
        presentation.refresh_from_db()
        other_event = make_event(make_community(name='別の集会'), event_date=own.confirmed_date)
        EventDetail.objects.filter(pk=presentation.published_event_detail_id).update(event=other_event)

        self.assertEqual(self._run()['confirmed'], 1)

        presentation.refresh_from_db()
        self.assertEqual(presentation.published_event_detail.event_id, own.published_event_id)

    def test_successful_change_replaces_own_old_slot(self):
        """自分の旧枠とは比較せず、変更の確定後は旧枠を別の参加へ渡せる。"""
        own = self._participation(applied_at=timezone.now() - timedelta(days=2))
        presentation = self._presentation(own)
        self._run()
        presentation.refresh_from_db()
        presentation.requested_start_time = time(22)
        presentation.save()
        own.requested_start_time = time(22)
        own.save()
        newcomer = self._participation(make_community(name='旧枠を希望する集会'))
        self._presentation(newcomer)
        self.assertEqual(self._run(), {'confirmed': 2, 'skipped': 0, 'incomplete': 0})
        own.refresh_from_db()
        newcomer.refresh_from_db()
        self.assertEqual(own.confirmed_start_time, time(22))
        self.assertIsNotNone(newcomer.published_event_id)
        self._assert_no_public_overlap([own, newcomer])

    def test_change_can_overlap_own_old_public_slot(self):
        """旧公開枠と一部重なる変更でも、自分自身とは競合しない。"""
        own = self._participation()
        presentation = self._presentation(own)
        self._run()
        presentation.refresh_from_db()
        presentation.requested_start_time = time(21, 10)
        presentation.save()
        self.assertEqual(self._run(), {'confirmed': 1, 'skipped': 0, 'incomplete': 0})
        presentation.refresh_from_db()
        self.assertEqual(presentation.confirmed_start_time, time(21, 10))
        self.assertEqual(presentation.published_event_detail.start_time, time(21, 10))

    def test_unconfirmed_addition_does_not_reserve_public_slot(self):
        """変更待ちの参加の追加希望は公開枠として扱わず、申込み順で判定する。"""
        own = self._participation()
        self._presentation(own)
        self._run()
        self._presentation(own, time(22), order=1)
        newcomer = self._participation(
            make_community(name='先の申込み'), start=time(22),
            applied_at=own.applied_at - timedelta(days=1),
        )
        self._presentation(newcomer, time(22))
        self.assertEqual(self._run(), {'confirmed': 1, 'skipped': 1, 'incomplete': 0})
        own.refresh_from_db()
        newcomer.refresh_from_db()
        self.assertEqual(EventDetail.objects.filter(event_id=own.published_event_id).count(), 1)
        self.assertIsNotNone(newcomer.published_event_id)
        self._assert_no_public_overlap([own, newcomer])

    def test_skipped_change_reserves_published_duration_before_requested_shortening(self):
        """発表時間の短縮希望が見送りでも、旧公開枠の後半へ別の参加は入れない。"""
        own = self._participation()
        presentation = self._presentation(own)
        presentation.duration = 60
        presentation.save()
        fixed = self._participation(make_community(name='固定集会'), start=time(22))
        self._presentation(fixed, time(22))
        self._run()
        presentation.refresh_from_db()
        presentation.requested_start_time = time(22)
        presentation.duration = 10
        presentation.save()
        newcomer = self._participation(make_community(name='旧枠後半の希望'), start=time(21, 30))
        self._presentation(newcomer, time(21, 30))
        self.assertEqual(self._run(), {'confirmed': 0, 'skipped': 2, 'incomplete': 0})
        own.refresh_from_db()
        newcomer.refresh_from_db()
        presentation.refresh_from_db()
        self.assertEqual(presentation.published_event_detail.duration, 60)
        self.assertIsNone(newcomer.published_event_id)
        self._assert_no_public_overlap([own, fixed, newcomer])

    def test_skipped_schedule_still_removes_withdrawn_public_presentation(self):
        """変更を見送っても取り下げは反映し、日程・他の公開発表は保持する。"""
        own = self._participation()
        kept = self._presentation(own)
        removed = self._presentation(own, time(21, 30), order=1)
        fixed = self._participation(make_community(name='固定集会'), start=time(22))
        self._presentation(fixed, time(22))
        self._run()
        own.refresh_from_db()
        kept.refresh_from_db()
        removed.refresh_from_db()
        confirmed_at = own.schedule_confirmed_at
        confirmed_date = own.confirmed_date
        removed_id = removed.published_event_detail_id
        delete_requested_presentation(removed)
        own.requested_start_time = time(22)
        own.requested_duration = 90
        own.save()
        kept.requested_start_time = time(22)
        kept.theme = '変更希望のテーマ'
        kept.save()
        with mock.patch('vket.services.clear_index_view_cache') as clear_cache:
            self.assertEqual(self._run(), {'confirmed': 0, 'skipped': 1, 'incomplete': 0})
        clear_cache.assert_called_once()
        own.refresh_from_db()
        kept.refresh_from_db()
        self.assertEqual(own.confirmed_date, confirmed_date)
        self.assertEqual(own.confirmed_start_time, time(21))
        self.assertEqual(own.confirmed_duration, 60)
        self.assertEqual(own.schedule_confirmed_at, confirmed_at)
        self.assertEqual(own.published_event.start_time, time(21))
        self.assertEqual(own.published_event.date, confirmed_date)
        self.assertEqual(own.published_event.duration, 60)
        self.assertEqual(kept.confirmed_start_time, time(21))
        self.assertEqual(kept.published_event_detail.start_time, time(21))
        self.assertEqual(kept.published_event_detail.theme, 'テーマ')
        self.assertFalse(EventDetail.objects.filter(pk=removed_id).exists())
        self.assertIsNotNone(EventDetail.all_objects.get(pk=removed_id).deleted_at)
        self.collaboration.refresh_from_db()
        self.assertNotIn(PENDING_DELETIONS_KEY, self.collaboration.settings_json)
        self.assertIn('activity_webhook_url', self.collaboration.settings_json)
        self._assert_no_public_overlap([own, fixed])

    def test_auto_confirmation_summary_normalizes_multiline_community_names(self):
        """確定行・重なりの組の両方で集会名の改行を一行にし、書式をエスケープする。"""
        self.community.name = '集会\n*名前*\r\n二行目'
        self.community.save()
        own = self._participation()
        self._presentation(own)
        other = self._participation(make_community(name='相手\n[集会]'))
        self._presentation(other)
        self._run()
        descriptions = [e['description'] for e in self.send.call_args.args[1]['embeds']]
        self.assertEqual(len(descriptions), 2)
        for description in descriptions:
            self.assertNotIn('\n', description)
            self.assertNotIn('\r', description)
            self.assertIn(r'集会 \*名前\* 二行目', description)
        self.assertIn(r'相手 \[集会\]', descriptions[1])

    def test_excluded_phases_inactive_unapplied_and_incomplete_are_not_confirmed(self):
        own = self._participation()
        self._presentation(own)
        for phase in (VketCollaboration.Phase.DRAFT, VketCollaboration.Phase.LOCKED, VketCollaboration.Phase.ARCHIVED):
            self.collaboration.phase = phase
            self.collaboration.save()
            self.assertEqual(self._run()['confirmed'], 0)
        self.collaboration.phase = VketCollaboration.Phase.ENTRY_OPEN
        self.collaboration.save()
        for lifecycle in (VketParticipation.Lifecycle.DECLINED, VketParticipation.Lifecycle.WITHDRAWN):
            own.lifecycle = lifecycle
            own.save()
            self.assertEqual(self._run()['confirmed'], 0)
        own.lifecycle = VketParticipation.Lifecycle.ACTIVE
        own.progress = VketParticipation.Progress.NOT_APPLIED
        own.save()
        self.assertEqual(self._run()['confirmed'], 0)
        own.progress = VketParticipation.Progress.APPLIED
        own.requested_date = None
        own.save()
        self.assertEqual(self._run()['incomplete'], 1)
        self.send.assert_not_called()

    def test_individual_delete_notifies_and_next_run_removes_last_public_presentation(self):
        own = self._participation()
        presentation = self._presentation(own)
        self._run()
        presentation.refresh_from_db()
        detail_id = presentation.published_event_detail_id
        self.send.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('vket:presentation_delete', kwargs={
                'pk': self.collaboration.pk, 'presentation_id': presentation.pk,
            }))
        self.assertEqual(response.status_code, 302)
        self.send.assert_called_once()
        self.assertTrue(EventDetail.objects.filter(pk=detail_id).exists())
        self.assertEqual(self._run()['confirmed'], 1)
        self.assertFalse(EventDetail.objects.filter(pk=detail_id).exists())
        self.assertEqual(self._run()['confirmed'], 0)

    def test_failed_summary_does_not_rollback_and_does_not_log_webhook_url(self):
        own = self._participation()
        self._presentation(own)
        url = self.collaboration.settings_json['activity_webhook_url']
        self.send.side_effect = RuntimeError(url)
        with self.assertLogs('vket.activity', level='WARNING') as logs:
            self.assertEqual(self._run()['confirmed'], 1)
        self.assertNotIn(url, ' '.join(logs.output))
        own.refresh_from_db()
        self.assertIsNotNone(own.published_event_id)

    def test_rollback_keeps_schedule_and_does_not_send_summary(self):
        own = self._participation()
        self._presentation(own)
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    auto_confirm_schedules()
                    raise RuntimeError('rollback')
            except RuntimeError:
                pass
        own.refresh_from_db()
        self.assertIsNone(own.confirmed_date)
        self.send.assert_not_called()

    def test_organizer_edit_and_delete_still_rejected_outside_receiving_phases(self):
        own = self._participation()
        presentation = self._presentation(own)
        self.collaboration.phase = VketCollaboration.Phase.LOCKED
        self.collaboration.save()
        data = {'requested_date': own.requested_date.isoformat(), 'requested_start_time': '22:00'}
        self.assertEqual(self.client.post(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}), data).status_code, 403)
        self.assertEqual(self.client.post(reverse('vket:presentation_delete', kwargs={
            'pk': self.collaboration.pk, 'presentation_id': presentation.pk,
        })).status_code, 403)

    def test_presentation_time_only_change_sends_activity_with_requested_time(self):
        own = self._participation()
        presentation = self._presentation(own)
        self._run()
        self.send.reset_mock()
        self._apply(own, [{'speaker': '登壇者', 'theme': 'テーマ', 'lt_start_time': '21:10'}],
                    start='21:00', duration='60', slot_minutes='30')
        self.send.assert_called_once()
        payload = self.send.call_args.args[1]
        self.assertIn('発表の変更', payload['content'])
        self.assertIn('21:10', payload['embeds'][0]['description'])
        presentation.refresh_from_db()
        self.assertEqual(presentation.confirmed_start_time, time(21))

    def test_auto_confirmation_locks_collaboration_before_participation(self):
        own = self._participation()
        self._presentation(own)
        locked_models = []

        def lock(queryset, *args, **kwargs):
            locked_models.append(queryset.model)
            return queryset

        with mock.patch('django.db.models.QuerySet.select_for_update', autospec=True, side_effect=lock):
            self.assertEqual(self._run()['confirmed'], 1)
        self.assertEqual(locked_models[:2], [VketCollaboration, VketParticipation])

    def test_summary_escapes_markdown_and_fits_payload_limits(self):
        from vket.activity import notify_auto_confirmation

        self.collaboration.name = '[x](https://example.com) *test*'
        lines = ['[x](https://example.com) *test* ' * 8] * 100
        with self.captureOnCommitCallbacks(execute=True):
            notify_auto_confirmation(self.collaboration, lines, lines)
        self.send.assert_called_once()
        payload = self.send.call_args.args[1]
        self.assertIn(r'\[x\]\(https://example.com\) \*test\*', payload['content'])
        self.assertLessEqual(len(payload['content']), 2000)
        self.assertLessEqual(sum(len(e['title']) + len(e['description']) for e in payload['embeds']), 6000)
        for embed in payload['embeds']:
            self.assertLessEqual(len(embed['description']), 4096)
            self.assertIn('ほか ', embed['description'])
            self.assertNotIn('[x](https://example.com)', embed['description'])
