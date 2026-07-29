"""Vketコラボ機能のテスト."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse

from community.models import Community
from event.models import EventDetail
from ta_hub.index_cache import get_index_view_cache_key
from vket.models import (
    VketParticipation,
    VketPresentation,
)


User = get_user_model()


from ._vket_test_bases import VketManageViewsBase


class VketManageViewsTests(VketManageViewsBase):
    def test_manage_update_confirms_draft_presentations(self):
        """確定ボタン押下でDRAFTのLTがCONFIRMEDに一括更新される"""
        self.client.login(username='admin_user', password='adminpass123')
        new_community = Community.objects.create(
            name='集会D', status='approved', frequency='毎週',
        )
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=new_community,
            requested_date=self.collaboration.period_start,
            requested_start_time='22:00',
            requested_duration=60,
        )
        draft_pres = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='DRAFT登壇者',
            theme='DRAFTテーマ',
            status=VketPresentation.Status.DRAFT,
        )
        confirmed_pres = VketPresentation.objects.create(
            participation=participation,
            order=1,
            speaker='確定済み登壇者',
            theme='確定済みテーマ',
            status=VketPresentation.Status.CONFIRMED,
        )

        new_date = self.collaboration.period_start + timedelta(days=1)
        self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': participation.pk,
                },
            ),
            data={
                'confirmed_date': new_date.isoformat(),
                'confirmed_start_time': '22:00',
                'confirmed_duration': '60',
            },
        )

        draft_pres.refresh_from_db()
        confirmed_pres.refresh_from_db()
        self.assertEqual(draft_pres.status, VketPresentation.Status.CONFIRMED)
        self.assertEqual(confirmed_pres.status, VketPresentation.Status.CONFIRMED)

    def test_manage_update_ignores_posted_start_time_for_initial_draft_presentation(self):
        """DRAFT発表の開始時刻は同一POSTで送られても更新しない"""
        self.client.login(username='admin_user', password='adminpass123')
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=Community.objects.create(name='集会E', status='approved', frequency='毎週'),
            requested_date=self.collaboration.period_start,
            requested_start_time='22:00',
            requested_duration=60,
        )
        draft_pres = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='DRAFT登壇者',
            theme='DRAFTテーマ',
            status=VketPresentation.Status.DRAFT,
        )

        new_date = self.collaboration.period_start + timedelta(days=1)
        self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': participation.pk,
                },
            ),
            data={
                'confirmed_date': new_date.isoformat(),
                'confirmed_start_time': '22:00',
                'confirmed_duration': '60',
                f'pres_{draft_pres.pk}_start_time': '22:15',
            },
        )

        draft_pres.refresh_from_db()
        self.assertEqual(draft_pres.status, VketPresentation.Status.CONFIRMED)
        self.assertIsNone(draft_pres.confirmed_start_time)

    def test_manage_update_creates_event_detail_for_new_presentation(self):
        """確定ボタンでpublished_event_detailがないCONFIRMED LTにEventDetailが作成される"""
        self.client.login(username='admin_user', password='adminpass123')
        # published_event_detail がない DRAFT のLTを追加
        pres = VketPresentation.objects.create(
            participation=self.participation1,
            order=1,
            speaker='新規登壇者',
            theme='新規テーマ',
            requested_start_time='21:45',
            status=VketPresentation.Status.DRAFT,
        )
        cache_key = get_index_view_cache_key()
        cache.set(cache_key, {'upcoming_event_details': ['stale']}, 60)
        new_date = self.collaboration.period_start + timedelta(days=1)
        self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': self.participation1.pk,
                },
            ),
            data={
                'confirmed_date': new_date.isoformat(),
                'confirmed_start_time': '21:00',
                'confirmed_duration': '60',
            },
        )
        pres.refresh_from_db()
        self.assertEqual(pres.status, VketPresentation.Status.CONFIRMED)
        self.assertIsNotNone(pres.published_event_detail)
        self.assertEqual(pres.published_event_detail.speaker, '新規登壇者')
        self.assertEqual(pres.published_event_detail.event_id, self.event1.id)
        self.assertIsNone(cache.get(cache_key))

    def test_manage_page_shows_draft_badge(self):
        """管理画面でDRAFTのLTに「申請中」バッジが表示される"""
        self.client.login(username='admin_user', password='adminpass123')
        VketPresentation.objects.create(
            participation=self.participation1,
            order=0,
            speaker='DRAFT登壇者',
            theme='DRAFTテーマ',
            status=VketPresentation.Status.DRAFT,
        )
        response = self.client.get(
            reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'DRAFT登壇者')
        self.assertContains(response, '申請中')
        self.assertContains(response, 'badge bg-warning')

    def test_manage_page_shows_time_input_for_rehearsal_confirmed_presentation(self):
        """公開前のCONFIRMED発表には開始時刻入力欄を表示する"""
        self.client.login(username='admin_user', password='adminpass123')
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=Community.objects.create(name='集会C', status='approved', frequency='毎週'),
            confirmed_date=self.collaboration.period_start,
            confirmed_start_time='21:00',
            confirmed_duration=60,
        )
        draft_pres = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='申請中登壇者',
            theme='申請中テーマ',
            status=VketPresentation.Status.DRAFT,
        )
        confirmed_pres = VketPresentation.objects.create(
            participation=participation,
            order=1,
            speaker='確定済み登壇者',
            theme='確定済みテーマ',
            requested_start_time='21:30',
            status=VketPresentation.Status.CONFIRMED,
        )

        response = self.client.get(
            reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'name="pres_{confirmed_pres.pk}_start_time"')
        self.assertContains(response, 'value="21:30"')
        self.assertNotContains(response, f'name="pres_{draft_pres.pk}_start_time"')

    def test_manage_page_prefers_event_detail_time_for_published_presentation(self):
        """公開済み発表の入力初期値は公開EventDetailの時刻を優先する"""
        self.client.login(username='admin_user', password='adminpass123')
        detail = EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            speaker='公開済み登壇者',
            theme='公開済みテーマ',
            start_time='22:15',
            duration=30,
            status='approved',
        )
        presentation = VketPresentation.objects.create(
            participation=self.participation1,
            order=0,
            speaker='公開済み登壇者',
            theme='公開済みテーマ',
            confirmed_start_time='21:30',
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=detail,
        )

        response = self.client.get(
            reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'name="pres_{presentation.pk}_start_time"')
        self.assertContains(response, 'value="22:15"')

    def test_manage_page_shows_lt_time_badge(self):
        """管理画面でLT時間バッジが表示される"""
        self.client.login(username='admin_user', password='adminpass123')
        # プレゼンテーションとEventDetailを作成
        detail = EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            start_time='21:30',
            duration=30,
            status='approved',
        )
        VketPresentation.objects.create(
            participation=self.participation1,
            order=0,
            speaker='テスト登壇者',
            theme='テストテーマ',
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=detail,
        )

        response = self.client.get(
            reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '21:30')
        self.assertContains(response, 'badge bg-info')

    def test_manage_presentation_delete_removes_presentation_and_event_detail(self):
        """管理者がLTを削除するとVketPresentationとEventDetailが両方削除される"""
        self.client.login(username='admin_user', password='adminpass123')
        detail = EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            start_time='21:30',
            duration=30,
            status='approved',
        )
        pres = VketPresentation.objects.create(
            participation=self.participation1,
            order=0,
            speaker='削除テスト登壇者',
            theme='削除テーマ',
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=detail,
        )
        response = self.client.post(
            reverse(
                'vket:manage_presentation_delete',
                kwargs={'pk': self.collaboration.pk, 'presentation_id': pres.pk},
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(VketPresentation.objects.filter(pk=pres.pk).exists())
        self.assertFalse(EventDetail.objects.filter(pk=detail.pk).exists())

    def test_manage_presentation_delete_requires_staff(self):
        """一般ユーザー（非staff）はLTを削除できない"""
        self.client.login(username='normal_user', password='testpass123')
        pres = VketPresentation.objects.create(
            participation=self.participation1,
            order=0,
            speaker='テスト',
            status=VketPresentation.Status.DRAFT,
        )
        response = self.client.post(
            reverse(
                'vket:manage_presentation_delete',
                kwargs={'pk': self.collaboration.pk, 'presentation_id': pres.pk},
            ),
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(VketPresentation.objects.filter(pk=pres.pk).exists())
