"""Vketコラボ機能のテスト."""

from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from community.models import Community, CommunityMember
from event.models import Event, EventDetail
from vket.models import (
    VketCollaboration,
    VketNotice,
    VketNoticeReceipt,
    VketParticipation,
    VketPresentation,
)


User = get_user_model()


class VketPublicPagesTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-2026-summer',
            name='Vket 2026 Summer 技術学術WEEK',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.ENTRY_OPEN,
            hashtags=['#Vketステージ', '#Vket技術学術WEEK'],
        )
        self.draft_collaboration = VketCollaboration.objects.create(
            slug='vket-2026-draft',
            name='Vket Draft',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.DRAFT,
        )

    def test_list_page(self):
        client = Client()
        response = client.get(reverse('vket:list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.collaboration.name)
        # 下書きは表示されない
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
            slug='invalid-collab',
            name='Invalid collaboration',
            period_start=today + timedelta(days=1),
            period_end=today,
            registration_deadline=today,
            lt_deadline=today - timedelta(days=1),
            phase=VketCollaboration.Phase.DRAFT,
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
            slug='vket-2026-apply-test',
            name='Vket 2026 Summer 技術学術WEEK',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.ENTRY_OPEN,
        )

    def _set_active_community(self):
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

    def test_apply_get_requires_owner(self):
        """スタッフはapplyページに403が返る"""
        self.client.login(username='other_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 403)

    def test_apply_post_creates_participation_and_presentation(self):
        """主催者が申請するとVketParticipationとVketPresentationが作成される"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        target_date = self.collaboration.period_start
        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data={
                'requested_date': target_date.isoformat(),
                'requested_start_time': '21:00',
                'requested_duration': '60',
                'speaker': 'テスト登壇者',
                'theme': 'テストテーマ',
                'organizer_note': '備考テスト',
            },
            follow=False,
        )

        self.assertEqual(response.status_code, 302)

        participation = VketParticipation.objects.get(
            collaboration=self.collaboration, community=self.community
        )
        # 希望日程が保存されている
        self.assertEqual(participation.requested_date, target_date)
        self.assertEqual(participation.requested_start_time.strftime('%H:%M'), '21:00')
        self.assertEqual(participation.requested_duration, 60)
        self.assertEqual(participation.organizer_note, '備考テスト')
        # applied_byがセットされている
        self.assertEqual(participation.applied_by, self.owner)
        self.assertIsNotNone(participation.applied_at)
        self.assertEqual(participation.progress, VketParticipation.Progress.APPLIED)

        # 確定前なのでEventは作成されない
        self.assertIsNone(participation.published_event_id)

        # VketPresentationが作成されている
        pres = VketPresentation.objects.get(participation=participation, order=0)
        self.assertEqual(pres.speaker, 'テスト登壇者')
        self.assertEqual(pres.theme, 'テストテーマ')

    def test_apply_is_forbidden_after_registration_deadline_for_new_participation(self):
        """参加申請締切後は新規参加登録が403になる"""
        self.collaboration.registration_deadline = timezone.localdate() - timedelta(days=1)
        self.collaboration.save()

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 403)

    def test_detail_can_apply_is_false_when_registration_closed_and_no_participation(self):
        """締切後・未参加の場合 can_apply=False"""
        self.collaboration.registration_deadline = timezone.localdate() - timedelta(days=1)
        self.collaboration.save()

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:detail', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_apply'])

    def test_detail_can_apply_is_true_when_lt_open_and_participation_has_published_event(self):
        """LT締切内かつpublished_eventがある場合 can_apply=True"""
        today = timezone.localdate()
        self.collaboration.registration_deadline = today - timedelta(days=1)
        self.collaboration.lt_deadline = today + timedelta(days=1)
        self.collaboration.phase = VketCollaboration.Phase.SCHEDULING
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
            published_event=event,
        )

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:detail', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_apply'])


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
            slug='vket-2026-manage-test',
            name='Vket 2026 Summer 技術学術WEEK',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.ENTRY_OPEN,
        )

        # 公開済みイベント（published_event）を使う
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
            published_event=self.event1,
            confirmed_date=today,
            confirmed_start_time='21:00',
            confirmed_duration=60,
        )

        self.participation2 = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community2,
            published_event=self.event2,
            confirmed_date=today,
            confirmed_start_time='21:30',
            confirmed_duration=60,
        )

    def test_manage_page_requires_superuser(self):
        """管理画面はsuperuserのみアクセス可能"""
        self.client.login(username='normal_user', password='testpass123')
        response = self.client.get(reverse('vket:manage', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 403)

    def test_manage_page_shows_collaboration(self):
        """管理画面にコラボ名と集会名が表示される"""
        self.client.login(username='admin_user', password='adminpass123')
        response = self.client.get(reverse('vket:manage', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.collaboration.name)
        self.assertContains(response, self.community1.name)

    def test_manage_schedule_page_shows_overlap_warning(self):
        """日程重複がある場合にoverlap_warningsがセットされる"""
        self.client.login(username='admin_user', password='adminpass123')
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

        self.client.login(username='admin_user', password='adminpass123')
        response = self.client.get(
            reverse('vket:manage_schedule', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)

        rows = response.context['rows']
        communities = [r['participation'].community.name for r in rows]
        self.assertIn('集会C', communities)

    def test_manage_schedule_page_marks_lt_slot(self):
        """LT詳細があるスロットにlt_timesが設定される"""
        EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            start_time='21:30',
            duration=30,
            status='approved',
        )

        self.client.login(username='admin_user', password='adminpass123')
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

    def test_manage_participation_update_sets_confirmed_fields(self):
        """ManageParticipationUpdateViewが確定日程・progressを正しくセットする"""
        self.client.login(username='admin_user', password='adminpass123')
        # 未確定の参加を作成してテスト
        new_participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=Community.objects.create(name='集会C', status='approved', frequency='毎週'),
            requested_date=self.collaboration.period_start,
            requested_start_time='22:00',
            requested_duration=60,
        )
        new_date = self.collaboration.period_start + timedelta(days=1)
        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': new_participation.pk,
                },
            ),
            data={
                'confirmed_date': new_date.isoformat(),
                'confirmed_start_time': '22:00',
                'confirmed_duration': '60',
                'admin_note': '確定しました',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        new_participation.refresh_from_db()
        self.assertEqual(new_participation.confirmed_date, new_date)
        self.assertEqual(new_participation.confirmed_start_time.strftime('%H:%M'), '22:00')
        self.assertEqual(new_participation.confirmed_duration, 60)
        self.assertEqual(new_participation.admin_note, '確定しました')
        self.assertTrue(new_participation.schedule_adjusted_by_admin)
        self.assertEqual(new_participation.progress, VketParticipation.Progress.SCHEDULE_CONFIRMED)
        self.assertIsNotNone(new_participation.schedule_confirmed_at)


class VketNoticeTests(TestCase):
    """お知らせ・ACK機能のテスト"""

    def setUp(self):
        self.client = Client()
        self.superuser = User.objects.create_superuser(
            user_name='admin_user2',
            email='admin2@example.com',
            password='adminpass123',
        )
        self.owner = User.objects.create_user(
            user_name='owner_user2',
            email='owner2@example.com',
            password='testpass123',
        )
        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週',
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER,
        )

        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-2026-notice-test',
            name='お知らせテスト',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.SCHEDULING,
        )
        self.participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )
        self.notice = VketNotice.objects.create(
            collaboration=self.collaboration,
            title='テストお知らせ',
            body='テスト本文',
            requires_ack=True,
            target_scope=VketNotice.TargetScope.ALL_PARTICIPANTS,
            created_by=self.superuser,
        )

    def test_ack_notice_view_get_does_not_mark_acknowledged(self):
        """AckNoticeView GET はプレビュー表示のみ（DB書き換えなし）"""
        receipt = VketNoticeReceipt.objects.create(
            notice=self.notice,
            participation=self.participation,
        )
        self.assertIsNone(receipt.acknowledged_at)

        response = self.client.get(
            reverse('vket:ack_notice', kwargs={'ack_token': str(receipt.ack_token)})
        )
        self.assertEqual(response.status_code, 200)

        receipt.refresh_from_db()
        self.assertIsNone(receipt.acknowledged_at)  # GETでは変更されない
        self.assertFalse(response.context['already_acked'])

    def test_ack_notice_view_post_marks_acknowledged(self):
        """AckNoticeView POST でreceiptのacknowledged_atがセットされる"""
        receipt = VketNoticeReceipt.objects.create(
            notice=self.notice,
            participation=self.participation,
        )
        self.assertIsNone(receipt.acknowledged_at)

        response = self.client.post(
            reverse('vket:ack_notice', kwargs={'ack_token': str(receipt.ack_token)})
        )
        self.assertEqual(response.status_code, 200)

        receipt.refresh_from_db()
        self.assertIsNotNone(receipt.acknowledged_at)
        self.assertTrue(response.context['already_acked'])

    def test_ack_notice_view_shows_already_acked(self):
        """2回目のアクセスはalready_acked=Trueになる"""
        receipt = VketNoticeReceipt.objects.create(
            notice=self.notice,
            participation=self.participation,
            acknowledged_at=timezone.now(),
        )
        response = self.client.get(
            reverse('vket:ack_notice', kwargs={'ack_token': str(receipt.ack_token)})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['already_acked'])

    def test_notice_list_view_requires_login(self):
        """お知らせ一覧はログイン必須"""
        response = self.client.get(
            reverse('vket:notice_list', kwargs={'pk': self.collaboration.pk})
        )
        # ログイン画面にリダイレクト
        self.assertEqual(response.status_code, 302)

    def test_manage_notice_list_requires_superuser(self):
        """管理用お知らせ一覧はsuperuserのみ"""
        self.client.login(username='owner_user2', password='testpass123')
        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 403)

    def test_manage_notice_list_shows_notice(self):
        """管理用お知らせ一覧にnoticeが表示される"""
        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.notice.title)

    def test_notice_create_auto_generates_receipts(self):
        """お知らせ作成時にactive参加者分のReceiptが自動生成される"""
        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_notice_create', kwargs={'pk': self.collaboration.pk}),
            data={
                'title': '自動生成テスト',
                'body': 'テスト本文',
                'target_scope': 'all',
                'requires_ack': 'on',
            },
        )
        self.assertEqual(response.status_code, 302)
        notice = VketNotice.objects.get(title='自動生成テスト')
        receipts = VketNoticeReceipt.objects.filter(notice=notice)
        self.assertEqual(receipts.count(), 1)
        self.assertEqual(receipts.first().participation, self.participation)


class VketPublishViewTests(TestCase):
    """ManagePublishViewのテスト"""

    def setUp(self):
        self.client = Client()
        self.superuser = User.objects.create_superuser(
            user_name='admin_pub',
            email='admin_pub@example.com',
            password='adminpass123',
        )
        self.community = Community.objects.create(
            name='公開テスト集会',
            status='approved',
            frequency='毎週',
        )
        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-2026-publish-test',
            name='公開テスト',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.LOCKED,
        )
        self.participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
            confirmed_date=today,
            confirmed_start_time='21:00',
            confirmed_duration=60,
        )

    def test_publish_creates_event_and_updates_participation(self):
        """公開処理でEventが作成されpublished_eventが紐づく"""
        self.client.login(username='admin_pub', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}),
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        self.participation.refresh_from_db()
        self.assertIsNotNone(self.participation.published_event_id)
        self.assertEqual(self.participation.progress, VketParticipation.Progress.DONE)

        event = self.participation.published_event
        self.assertEqual(event.community, self.community)
        self.assertEqual(event.start_time.strftime('%H:%M'), '21:00')
        self.assertEqual(event.duration, 60)

    def test_publish_is_forbidden_when_not_locked(self):
        """LOCKEDフェーズ以外では公開処理が403になる"""
        self.collaboration.phase = VketCollaboration.Phase.SCHEDULING
        self.collaboration.save()

        self.client.login(username='admin_pub', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}),
        )
        self.assertEqual(response.status_code, 403)

    def test_publish_is_idempotent(self):
        """公開処理を2回実行しても同じEventが使われ、重複作成されない"""
        from event.models import Event

        self.client.login(username='admin_pub', password='adminpass123')
        url = reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk})

        # 1回目
        self.client.post(url)
        self.participation.refresh_from_db()
        first_event_id = self.participation.published_event_id
        self.assertIsNotNone(first_event_id)

        # 2回目
        self.client.post(url)
        self.participation.refresh_from_db()
        # 同じEventが使われていること
        self.assertEqual(self.participation.published_event_id, first_event_id)
        # Eventが重複作成されていないこと
        self.assertEqual(Event.objects.filter(community=self.community).count(), 1)
