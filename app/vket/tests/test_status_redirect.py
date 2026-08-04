"""Vketコラボ機能のテスト."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from community.models import Community, CommunityMember
from vket.models import (
    VketCollaboration,
    VketParticipation,
)


User = get_user_model()


class VketStatusRedirectViewTests(TestCase):
    """VketStatusRedirectView のテスト"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            user_name='redirect_user',
            email='redirect@example.com',
            password='testpass123',
        )
        self.superuser = User.objects.create_superuser(
            user_name='redirect_admin',
            email='redirect_admin@example.com',
            password='adminpass123',
        )
        self.community = Community.objects.create(
            name='リダイレクトテスト集会',
            status='approved',
            frequency='毎週',
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.user,
            role=CommunityMember.Role.OWNER,
        )

        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-2026-redirect-test',
            name='リダイレクトテスト',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.ENTRY_OPEN,
        )

    def test_redirect_requires_login(self):
        """未ログインで302（ログインページへ）"""
        response = self.client.get(reverse('vket:status_redirect'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_redirect_to_latest_collaboration(self):
        """ログインユーザーで最新コラボのstatusへリダイレクト"""
        self.client.force_login(self.user)
        response = self.client.get(reverse('vket:status_redirect'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse('vket:status', kwargs={'pk': self.collaboration.pk}),
        )

    def test_redirect_to_list_when_only_archived(self):
        """ARCHIVEDのみの場合、公開一覧にリダイレクト"""
        self.collaboration.phase = VketCollaboration.Phase.ARCHIVED
        self.collaboration.save()

        self.client.force_login(self.user)
        response = self.client.get(reverse('vket:status_redirect'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('vket:list'))

    def test_redirect_skips_draft_for_non_superuser(self):
        """非superuserでDRAFTのみの場合、公開一覧にリダイレクト"""
        self.collaboration.phase = VketCollaboration.Phase.DRAFT
        self.collaboration.save()

        self.client.force_login(self.user)
        response = self.client.get(reverse('vket:status_redirect'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('vket:list'))

    def test_superuser_can_access_draft(self):
        """superuserはDRAFTコラボにもリダイレクトされる"""
        self.collaboration.phase = VketCollaboration.Phase.DRAFT
        self.collaboration.save()

        self.client.force_login(self.superuser)
        response = self.client.get(reverse('vket:status_redirect'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse('vket:status', kwargs={'pk': self.collaboration.pk}),
        )

    def test_status_page_shows_collaboration_dropdown(self):
        """コラボ切替ドロップダウンが status ページに表示される"""
        today = timezone.localdate()
        second = VketCollaboration.objects.create(
            slug='vket-2026-redirect-test-2',
            name='リダイレクトテスト2',
            period_start=today - timedelta(days=30),
            period_end=today - timedelta(days=23),
            registration_deadline=today - timedelta(days=29),
            lt_deadline=today - timedelta(days=27),
            phase=VketCollaboration.Phase.LOCKED,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()
        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '切替')
        self.assertContains(response, second.name)

    def test_status_page_shows_admin_link_for_superuser(self):
        """superuserで管理者リンク（管理画面）が表示される"""
        # superuserにもCommunityMembershipが必要
        CommunityMember.objects.create(
            community=self.community,
            user=self.superuser,
            role=CommunityMember.Role.OWNER,
        )
        self.client.force_login(self.superuser)
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        # participationを作成（管理者リンクはアクションボタンエリアに表示）
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )

        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'fa-gear')

    def test_status_page_hides_admin_link_for_normal_user(self):
        """一般ユーザーには管理者リンクが表示されない"""
        self.client.force_login(self.user)
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )

        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'fa-gear')
