"""Vketコラボ機能のテスト."""

from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from community.models import Community, CommunityMember
from event.models import Event, EventDetail
from vket.models import VketCollaboration, VketParticipation


User = get_user_model()


class VketPublicPagesTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            name='Vket 2026 Summer 技術学術WEEK',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            status=VketCollaboration.Status.OPEN,
            hashtags=['#Vketステージ', '#Vket技術学術WEEK'],
        )
        self.draft_collaboration = VketCollaboration.objects.create(
            name='Vket Draft',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            status=VketCollaboration.Status.DRAFT,
        )

    def test_list_page(self):
        client = Client()
        response = client.get(reverse('vket:list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vketコラボ')
        self.assertContains(response, self.collaboration.name)
        self.assertNotContains(response, self.draft_collaboration.name)

    def test_detail_page(self):
        client = Client()
        response = client.get(reverse('vket:detail', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.collaboration.name)

    def test_detail_page_is_404_for_draft(self):
        client = Client()
        response = client.get(reverse('vket:detail', kwargs={'pk': self.draft_collaboration.pk}))
        self.assertEqual(response.status_code, 404)

    def test_collaboration_validation(self):
        today = timezone.localdate()
        collaboration = VketCollaboration(
            name='Invalid collaboration',
            period_start=today + timedelta(days=1),
            period_end=today,
            registration_deadline=today,
            lt_deadline=today - timedelta(days=1),
            status=VketCollaboration.Status.DRAFT,
        )
        with self.assertRaises(ValidationError):
            collaboration.full_clean()


class VketApplyFlowTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(
            user_name='owner_user',
            email='owner@example.com',
            password='testpass123',
        )
        self.other_user = User.objects.create_user(
            user_name='other_user',
            email='other@example.com',
            password='testpass123',
        )

        self.community = Community.objects.create(
            name='個人開発集会',
            status='approved',
            frequency='毎週',
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER,
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.other_user,
            role=CommunityMember.Role.STAFF,
        )

        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            name='Vket 2026 Summer 技術学術WEEK',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            status=VketCollaboration.Status.OPEN,
        )

    def _set_active_community(self):
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

    def test_apply_get_requires_owner(self):
        self.client.login(username='other_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 403)

    def test_apply_post_creates_participation_event_and_lt(self):
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        target_date = self.collaboration.period_start
        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data={
                'participation_date': target_date.isoformat(),
                'start_time': '21:00',
                'duration': '60',
                'speaker': 'テスト登壇者',
                'theme': 'テストテーマ',
                'note': '備考テスト',
            },
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        participation = VketParticipation.objects.get(collaboration=self.collaboration, community=self.community)
        self.assertIsNotNone(participation.event_id)
        self.assertEqual(participation.note, '備考テスト')

        event = participation.event
        self.assertEqual(event.date, target_date)
        self.assertEqual(event.start_time.strftime('%H:%M'), '21:00')
        self.assertEqual(event.duration, 60)

        lt = EventDetail.objects.get(event=event, detail_type='LT')
        self.assertEqual(lt.speaker, 'テスト登壇者')
        self.assertEqual(lt.theme, 'テストテーマ')
        self.assertEqual(lt.start_time.strftime('%H:%M'), '21:00')
        self.assertEqual(lt.duration, 60)

    def test_apply_is_forbidden_after_registration_deadline_for_new_participation(self):
        self.collaboration.registration_deadline = timezone.localdate() - timedelta(days=1)
        self.collaboration.save()

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 403)

    def test_detail_can_apply_is_false_when_registration_closed_and_no_participation(self):
        self.collaboration.registration_deadline = timezone.localdate() - timedelta(days=1)
        self.collaboration.save()

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:detail', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_apply'])

    def test_detail_can_apply_is_true_when_lt_open_and_participation_has_event(self):
        today = timezone.localdate()
        self.collaboration.registration_deadline = today - timedelta(days=1)
        self.collaboration.lt_deadline = today + timedelta(days=1)
        self.collaboration.save()

        weekday_code = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][today.weekday()]
        event = Event.objects.create(
            community=self.community,
            date=today,
            start_time='21:00',
            duration=60,
            weekday=weekday_code,
        )
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            event=event,
        )

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:detail', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_apply'])

    def test_apply_is_forbidden_when_participation_has_no_event_and_schedule_locked(self):
        today = timezone.localdate()
        self.collaboration.registration_deadline = today - timedelta(days=1)
        self.collaboration.lt_deadline = today + timedelta(days=1)
        self.collaboration.save()

        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            event=None,
        )

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 403)


class VketManageViewsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.superuser = User.objects.create_superuser(
            user_name='admin_user',
            email='admin@example.com',
            password='adminpass123',
        )
        self.normal_user = User.objects.create_user(
            user_name='normal_user',
            email='normal@example.com',
            password='testpass123',
        )

        self.community1 = Community.objects.create(
            name='集会A',
            status='approved',
            frequency='毎週',
        )
        self.community2 = Community.objects.create(
            name='集会B',
            status='approved',
            frequency='毎週',
        )

        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            name='Vket 2026 Summer 技術学術WEEK',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            status=VketCollaboration.Status.OPEN,
        )

        self.event1 = Event.objects.create(
            community=self.community1,
            date=today,
            start_time='21:00',
            duration=60,
            weekday='Tue',
        )
        self.event2 = Event.objects.create(
            community=self.community2,
            date=today,
            start_time='21:30',
            duration=60,
            weekday='Tue',
        )

        self.participation1 = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community1,
            event=self.event1,
        )

        self.participation2 = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community2,
            event=self.event2,
        )

    def test_manage_speakers_page(self):
        self.client.login(username='admin_user', password='adminpass123')
        response = self.client.get(reverse('vket:manage', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.collaboration.name)
        self.assertContains(response, self.community1.name)

    def test_manage_pages_require_superuser(self):
        self.client.login(username='normal_user', password='testpass123')
        response = self.client.get(reverse('vket:manage', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 403)

    def test_manage_schedule_page_shows_overlap_warning(self):
        self.client.login(username='admin_user', password='adminpass123')
        response = self.client.get(reverse('vket:manage_schedule', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['overlap_warnings'])
        self.assertContains(response, '重複（開催時間）')
        self.assertContains(response, 'vket-bar overlap')

    def test_manage_schedule_page_marks_lt_slot(self):
        EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            start_time='21:30',
            duration=30,
            status='approved',
        )

        self.client.login(username='admin_user', password='adminpass123')
        response = self.client.get(reverse('vket:manage_schedule', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)

        slots = response.context['slots']
        expected_idx = next(i for i, s in enumerate(slots) if s.start == time(21, 30))

        rows = response.context['rows']
        row = next(r for r in rows if r['participation'].pk == self.participation1.pk)
        lt_indices = [i for i, cell in enumerate(row['cells']) if cell.get('lt')]
        self.assertEqual(lt_indices, [expected_idx])

    def test_manage_schedule_page_shows_lt_overlap_warning(self):
        EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            start_time='21:30',
            duration=30,
            status='approved',
        )
        EventDetail.objects.create(
            event=self.event2,
            detail_type='LT',
            start_time='21:30',
            duration=30,
            status='approved',
        )

        self.client.login(username='admin_user', password='adminpass123')
        response = self.client.get(reverse('vket:manage_schedule', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['warnings'])
        self.assertContains(response, '⚠️ 警告')
        self.assertContains(response, 'LT開始が重複')
        self.assertContains(response, 'vket-lt-dot overlap')

    def test_manage_schedule_page_warns_when_lt_outside_event_range(self):
        EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            start_time='20:00',
            duration=30,
            status='approved',
        )

        self.client.login(username='admin_user', password='adminpass123')
        response = self.client.get(reverse('vket:manage_schedule', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['warnings'])
        self.assertContains(response, '開催時間')

        slots = response.context['slots']
        expected_idx = next(i for i, s in enumerate(slots) if s.start == time(20, 0))

        rows = response.context['rows']
        row = next(r for r in rows if r['participation'].pk == self.participation1.pk)
        lt_indices = [i for i, cell in enumerate(row['cells']) if cell.get('lt')]
        self.assertEqual(lt_indices, [expected_idx])

    def test_manage_update_updates_event_and_admin_note(self):
        self.client.login(username='admin_user', password='adminpass123')
        new_date = self.collaboration.period_start + timedelta(days=1)
        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={'pk': self.collaboration.pk, 'participation_id': self.participation1.pk},
            ),
            data={
                'date': new_date.isoformat(),
                'start_time': '22:00',
                'admin_note': '要確認',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        self.participation1.refresh_from_db()
        self.event1.refresh_from_db()
        self.assertEqual(self.participation1.admin_note, '要確認')
        self.assertEqual(self.event1.date, new_date)
        self.assertEqual(self.event1.start_time.strftime('%H:%M'), '22:00')
