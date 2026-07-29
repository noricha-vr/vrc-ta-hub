"""Vketコラボ機能のテスト."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from community.models import Community, CommunityMember
from vket.models import (
    VketCollaboration,
    VketNotice,
    VketNoticeReceipt,
    VketParticipation,
)


User = get_user_model()


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

    def test_ack_notice_view_links_back_to_open_notice_detail(self):
        """ACK済み画面から対象のお知らせ詳細を開いた一覧へ戻れる"""
        receipt = VketNoticeReceipt.objects.create(
            notice=self.notice,
            participation=self.participation,
            acknowledged_at=timezone.now(),
        )

        response = self.client.get(
            reverse('vket:ack_notice', kwargs={'ack_token': str(receipt.ack_token)})
        )

        self.assertEqual(response.status_code, 200)
        detail_url = (
            f"{reverse('vket:notice_list', kwargs={'pk': self.collaboration.pk})}"
            f"?open={self.notice.pk}"
        )
        self.assertContains(response, detail_url)
        self.assertContains(response, 'お知らせ一覧で詳細を見る')

    def test_notice_list_view_requires_login(self):
        """お知らせ一覧はログイン必須"""
        response = self.client.get(
            reverse('vket:notice_list', kwargs={'pk': self.collaboration.pk})
        )
        # ログイン画面にリダイレクト
        self.assertEqual(response.status_code, 302)

    def test_notice_list_view_shows_acked_notice_detail(self):
        """参加者側一覧はACK済みでもお知らせ本文を開ける"""
        self.notice.body = '1行目の詳細\n\n"引用" と & 記号を含む本文'
        self.notice.save(update_fields=['body'])
        VketNoticeReceipt.objects.create(
            notice=self.notice,
            participation=self.participation,
            acknowledged_at=timezone.now(),
        )

        self.client.login(username='owner_user2', password='testpass123')
        response = self.client.get(
            reverse('vket:notice_list', kwargs={'pk': self.collaboration.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.notice.title)
        self.assertContains(response, '確認済み')
        self.assertContains(response, f'data-bs-target="#notice-{self.notice.pk}"')
        self.assertContains(response, f'id="notice-{self.notice.pk}"')
        self.assertContains(response, '1行目の詳細')
        self.assertContains(response, '引用')

    def test_manage_notice_list_requires_staff(self):
        """管理用お知らせ一覧はstaff権限が必要"""
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

    def test_manage_notice_list_shows_acked_notice_detail_modal(self):
        """管理一覧はACK済みでも全文詳細モーダルを表示する"""
        self.notice.body = '管理向け詳細本文\n\n"引用" と & 記号を含む本文'
        self.notice.save(update_fields=['body'])
        VketNoticeReceipt.objects.create(
            notice=self.notice,
            participation=self.participation,
            acknowledged_at=timezone.now(),
        )

        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'noticeDetailModal{self.notice.pk}')
        self.assertContains(response, '管理向け詳細本文')
        self.assertContains(response, '引用')
        self.assertContains(response, '1/1確認')
        self.assertContains(response, '作成日時')

    def test_status_page_latest_notice_links_to_open_detail(self):
        """参加状況ページの最新お知らせから対象詳細を開ける"""
        VketNoticeReceipt.objects.create(
            notice=self.notice,
            participation=self.participation,
            acknowledged_at=timezone.now(),
        )

        self.client.login(username='owner_user2', password='testpass123')
        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )

        self.assertEqual(response.status_code, 200)
        detail_url = (
            f"{reverse('vket:notice_list', kwargs={'pk': self.collaboration.pk})}"
            f"?open={self.notice.pk}"
        )
        self.assertContains(response, detail_url, count=2)
        self.assertContains(response, '詳細')

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

    def test_manage_notice_update_success(self):
        """superuserがお知らせのタイトルと本文を編集できる"""
        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_notice_update', kwargs={
                'pk': self.collaboration.pk,
                'notice_id': self.notice.pk,
            }),
            data={'title': '更新タイトル', 'body': '更新本文'},
        )
        self.assertEqual(response.status_code, 302)
        self.notice.refresh_from_db()
        self.assertEqual(self.notice.title, '更新タイトル')
        self.assertEqual(self.notice.body, '更新本文')

    def test_manage_notice_update_requires_staff(self):
        """一般ユーザー（非staff）はお知らせを編集できない"""
        self.client.login(username='owner_user2', password='testpass123')
        response = self.client.post(
            reverse('vket:manage_notice_update', kwargs={
                'pk': self.collaboration.pk,
                'notice_id': self.notice.pk,
            }),
            data={'title': '不正更新', 'body': '不正本文'},
        )
        self.assertEqual(response.status_code, 403)
        self.notice.refresh_from_db()
        self.assertEqual(self.notice.title, 'テストお知らせ')

    def test_manage_notice_update_validates_required_fields(self):
        """タイトル・本文が空の場合はエラー"""
        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_notice_update', kwargs={
                'pk': self.collaboration.pk,
                'notice_id': self.notice.pk,
            }),
            data={'title': '', 'body': ''},
        )
        self.assertEqual(response.status_code, 302)
        self.notice.refresh_from_db()
        self.assertEqual(self.notice.title, 'テストお知らせ')

    def test_manage_notice_update_rejects_other_collaboration_notice(self):
        """他のcollaborationのお知らせは編集できない（404）"""
        other_collab = VketCollaboration.objects.create(
            slug='other-collab',
            name='別コラボ',
            period_start=timezone.localdate(),
            period_end=timezone.localdate() + timedelta(days=7),
            registration_deadline=timezone.localdate() + timedelta(days=1),
            lt_deadline=timezone.localdate() + timedelta(days=3),
            phase=VketCollaboration.Phase.SCHEDULING,
        )
        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_notice_update', kwargs={
                'pk': other_collab.pk,
                'notice_id': self.notice.pk,
            }),
            data={'title': '不正更新', 'body': '不正本文'},
        )
        self.assertEqual(response.status_code, 404)

    def test_manage_notice_list_unacked_mentions_role(self):
        """未ACKのコミュニティのロールメンションがコンテキストに含まれる"""
        self.community.discord_mention_type = Community.DiscordMentionType.ROLE
        self.community.discord_mention_role_id = '111222333'
        self.community.save()

        VketNoticeReceipt.objects.create(
            notice=self.notice, participation=self.participation
        )

        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )
        stat = response.context['notice_stats'][0]
        self.assertIn('<@&111222333>', stat['unacked_mentions'])

    def test_manage_notice_list_unacked_mentions_users(self):
        """未ACKのコミュニティのユーザーメンションがコンテキストに含まれる"""
        self.community.discord_mention_type = Community.DiscordMentionType.USERS
        self.community.discord_mention_user_ids = ['aaa', 'bbb']
        self.community.save()

        VketNoticeReceipt.objects.create(
            notice=self.notice, participation=self.participation
        )

        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )
        stat = response.context['notice_stats'][0]
        self.assertIn('<@aaa>', stat['unacked_mentions'])
        self.assertIn('<@bbb>', stat['unacked_mentions'])

    def test_manage_notice_list_acked_not_in_mentions(self):
        """ACK済みのコミュニティはメンションに含まれない"""
        self.community.discord_mention_type = Community.DiscordMentionType.ROLE
        self.community.discord_mention_role_id = '999888777'
        self.community.save()

        VketNoticeReceipt.objects.create(
            notice=self.notice,
            participation=self.participation,
            acknowledged_at=timezone.now(),
        )

        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )
        stat = response.context['notice_stats'][0]
        self.assertEqual(stat['unacked_mentions'], [])

    def test_manage_notice_list_unacked_mentions_fallback_discord_id(self):
        """メンション未設定のコミュニティはメンバーのDiscord IDでメンション生成"""
        from allauth.socialaccount.models import SocialAccount
        SocialAccount.objects.create(
            user=self.owner, provider='discord', uid='123456789'
        )
        VketNoticeReceipt.objects.create(
            notice=self.notice, participation=self.participation
        )

        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )
        stat = response.context['notice_stats'][0]
        self.assertIn('<@123456789>', stat['unacked_mentions'])
