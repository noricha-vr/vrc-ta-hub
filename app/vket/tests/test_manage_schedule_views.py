"""Vketコラボ機能のテスト."""

from datetime import time

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from community.models import Community
from event.models import EventDetail
from vket.models import (
    VketParticipation,
    VketPresentation,
)
from vket.views.helpers import _build_schedule_context


User = get_user_model()


from ._vket_test_bases import VketApplyFlowBase, VketManageViewsBase


class VketApplyFlowTests(VketApplyFlowBase):
    def test_schedule_table_uses_unpublished_presentation_start_time(self):
        """未公開の発表開始時刻が日程表のLTマーカーに反映される"""
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time=time(21, 0),
            requested_duration=60,
            progress=VketParticipation.Progress.STAGE_REGISTERED,
        )
        VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='未定',
            theme='未定',
            requested_start_time=time(21, 30),
            duration=30,
        )

        context = _build_schedule_context(self.collaboration, include_requested=True)
        row = next(
            r for r in context['rows'] if r['participation'].pk == participation.pk
        )
        lt_tooltips = [
            cell['lt_tooltip'] for cell in row['cells'] if cell['lt_times']
        ]

        self.assertEqual(row['start_time'], time(21, 0))
        self.assertEqual(lt_tooltips, ['21:30'])


class VketManageViewsTests(VketManageViewsBase):
    def test_manage_page_requires_staff(self):
        """管理画面はstaff権限が必要"""
        self.client.force_login(self.normal_user)
        response = self.client.get(reverse('vket:manage', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 403)

    def test_manage_page_shows_collaboration(self):
        """管理画面にコラボ名と集会名が表示される"""
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('vket:manage', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.collaboration.name)
        self.assertContains(response, self.community1.name)
        self.assertContains(response, 'name="lifecycle"')
        self.assertContains(response, '不参加')
        self.assertContains(response, '辞退')

    def test_manage_schedule_page_shows_overlap_warning(self):
        """日程重複がある場合にoverlap_warningsがセットされる"""
        self.client.force_login(self.superuser)
        response = self.client.get(
            reverse('vket:manage_schedule', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['overlap_warnings'])

    def test_manage_schedule_shows_confirmed_without_published_event(self):
        """confirmed_dateがあればpublished_eventなしでも日程表に表示される"""
        community3 = Community.objects.create(name='集会C', status='approved', frequency='毎週')
        today = timezone.localdate()
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=community3,
            confirmed_date=today,
            confirmed_start_time='22:00',
            confirmed_duration=60,
        )

        self.client.force_login(self.superuser)
        response = self.client.get(
            reverse('vket:manage_schedule', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)

        rows = response.context['rows']
        communities = [r['participation'].community.name for r in rows]
        self.assertIn('集会C', communities)

    def test_manage_schedule_shows_requested_without_confirmed_schedule(self):
        """未確定でも希望日程があれば管理日程表に表示される"""
        community3 = Community.objects.create(name='集会C', status='approved', frequency='毎週')
        today = timezone.localdate()
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=community3,
            requested_date=today,
            requested_start_time='22:00',
            requested_duration=60,
        )

        self.client.force_login(self.superuser)
        response = self.client.get(
            reverse('vket:manage_schedule', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)

        rows = response.context['rows']
        requested_row = next(
            r for r in rows if r['participation'].community.name == '集会C'
        )
        self.assertFalse(requested_row['is_confirmed'])
        self.assertEqual(requested_row['date'], today)
        self.assertEqual(requested_row['start_time'], time(22, 0))
        self.assertContains(response, '集会C')
        self.assertContains(response, '申請中')

    def test_manage_schedule_page_marks_lt_slot(self):
        """LT詳細があるスロットにlt_timesが設定される"""
        EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            start_time='21:30',
            duration=30,
            status='approved',
        )

        self.client.force_login(self.superuser)
        response = self.client.get(
            reverse('vket:manage_schedule', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)

        slots = response.context['slots']
        expected_idx = next(i for i, s in enumerate(slots) if s.start == time(21, 30))

        rows = response.context['rows']
        row = next(r for r in rows if r['participation'].pk == self.participation1.pk)
        lt_indices = [i for i, cell in enumerate(row['cells']) if cell.get('lt_times')]
        self.assertEqual(lt_indices, [expected_idx])
        self.assertEqual(row['cells'][expected_idx]['lt_times'], [time(21, 30)])
