"""LT申請一覧・編集ビューのテスト"""
from datetime import date, time

from django.test import TestCase, Client
from django.urls import reverse

from community.models import Community, CommunityMember
from event.models import Event, EventDetail
from user_account.tests.utils import create_discord_linked_user


class LTApplicationViewTestBase(TestCase):
    """LT申請ビューテストの共通セットアップ"""

    def setUp(self):
        self.client = Client()

        # 申請者ユーザー
        self.user = create_discord_linked_user(
            user_name='applicant',
            email='applicant@example.com',
            password='testpass123',
        )

        # 他ユーザー
        self.other_user = create_discord_linked_user(
            user_name='other_user',
            email='other@example.com',
            password='testpass123',
        )

        # コミュニティ
        self.community = Community.objects.create(
            name='Test Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
        )

        # イベント
        self.event = Event.objects.create(
            community=self.community,
            date=date(2026, 3, 1),
            start_time=time(22, 0),
            duration=60,
        )

        # 自分のLT申請（承認済み）
        self.my_application = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='My LT Theme',
            speaker='applicant',
            start_time=time(22, 0),
            duration=15,
            status='approved',
            applicant=self.user,
        )

        # 自分のLT申請（却下）
        self.my_rejected = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Rejected Theme',
            speaker='applicant',
            start_time=time(22, 15),
            duration=15,
            status='rejected',
            applicant=self.user,
            rejection_reason='テスト却下理由',
        )

        # 他ユーザーのLT申請
        self.other_application = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Other Theme',
            speaker='other_user',
            start_time=time(22, 30),
            duration=15,
            status='approved',
            applicant=self.other_user,
        )

        # 自分のBLOG（LT以外）
        self.my_blog = EventDetail.objects.create(
            event=self.event,
            detail_type='BLOG',
            theme='Blog Theme',
            speaker='applicant',
            start_time=time(22, 45),
            duration=30,
            applicant=self.user,
        )

        self.list_url = reverse('account:lt_application_list')
        self.edit_url = reverse('account:lt_application_edit', kwargs={'pk': self.my_application.pk})


class LTApplicationListViewTests(LTApplicationViewTestBase):
    """LT申請一覧ビューのテスト"""

    def test_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_authenticated_shows_own_applications(self):
        self.client.login(username='applicant', password='testpass123')
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        applications = response.context['applications']
        # 自分のLT申請のみ（2件: approved + rejected）
        self.assertEqual(len(applications), 2)
        themes = [a.theme for a in applications]
        self.assertIn('My LT Theme', themes)
        self.assertIn('Rejected Theme', themes)

    def test_other_user_applications_not_shown(self):
        self.client.login(username='applicant', password='testpass123')
        response = self.client.get(self.list_url)
        applications = response.context['applications']
        themes = [a.theme for a in applications]
        self.assertNotIn('Other Theme', themes)

    def test_non_lt_type_not_shown(self):
        self.client.login(username='applicant', password='testpass123')
        response = self.client.get(self.list_url)
        applications = response.context['applications']
        themes = [a.theme for a in applications]
        self.assertNotIn('Blog Theme', themes)

    def test_rejected_reason_displayed(self):
        self.client.login(username='applicant', password='testpass123')
        response = self.client.get(self.list_url)
        self.assertContains(response, 'テスト却下理由')


class LTApplicationEditViewTests(LTApplicationViewTestBase):
    """LT申請編集ビューのテスト"""

    def test_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.edit_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_can_edit_own_application(self):
        self.client.login(username='applicant', password='testpass123')
        response = self.client.get(self.edit_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'My LT Theme')

    def test_cannot_edit_other_user_application(self):
        self.client.login(username='applicant', password='testpass123')
        other_edit_url = reverse('account:lt_application_edit', kwargs={'pk': self.other_application.pk})
        response = self.client.get(other_edit_url)
        self.assertEqual(response.status_code, 404)

    def test_save_theme_and_speaker(self):
        self.client.login(username='applicant', password='testpass123')
        response = self.client.post(self.edit_url, {
            'theme': 'Updated Theme',
            'speaker': 'Updated Speaker',
            'slide_url': '',
            'youtube_url': '',
            'h1': '',
            'contents': '',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.my_application.refresh_from_db()
        self.assertEqual(self.my_application.theme, 'Updated Theme')
        self.assertEqual(self.my_application.speaker, 'Updated Speaker')

    def test_start_time_and_duration_not_changed(self):
        self.client.login(username='applicant', password='testpass123')
        original_start = self.my_application.start_time
        original_duration = self.my_application.duration
        self.client.post(self.edit_url, {
            'theme': 'Updated Theme',
            'speaker': 'applicant',
            'slide_url': '',
            'youtube_url': '',
            'h1': '',
            'contents': '',
        })
        self.my_application.refresh_from_db()
        self.assertEqual(self.my_application.start_time, original_start)
        self.assertEqual(self.my_application.duration, original_duration)

    def test_status_not_changed_by_form(self):
        self.client.login(username='applicant', password='testpass123')
        self.client.post(self.edit_url, {
            'theme': 'Updated Theme',
            'speaker': 'applicant',
            'slide_url': '',
            'youtube_url': '',
            'h1': '',
            'contents': '',
            'status': 'rejected',  # 改竄を試みる
        })
        self.my_application.refresh_from_db()
        self.assertEqual(self.my_application.status, 'approved')
