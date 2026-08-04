"""Vketコラボ機能のテスト."""

from datetime import timedelta
from html import escape
from urllib.parse import urlencode
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
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
            pk=1,
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

    def test_anonymous_notice_list_redirects_other_pk_without_db_query(self):
        """公開対象外のpkは存在確認せずログインへリダイレクトする"""
        url = reverse('vket:notice_list', kwargs={'pk': 9999})

        with self.assertNumQueries(0):
            response = self.client.get(url)

        self.assertRedirects(
            response,
            f'{settings.LOGIN_URL}?next={url}',
            fetch_redirect_response=False,
        )

    def test_anonymous_notice_list_is_public_shell_without_private_data_queries(self):
        """公開シェルは機密データを取得せず表示もしない"""
        receipt = VketNoticeReceipt.objects.create(
            notice=self.notice,
            participation=self.participation,
            acknowledged_at=timezone.now(),
        )
        url = reverse('vket:notice_list', kwargs={'pk': self.collaboration.pk})

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)

        sql = ' '.join(query['sql'].lower() for query in queries.captured_queries)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'vket/notice_public.html')
        self.assertNotContains(response, self.notice.title)
        self.assertNotContains(response, self.notice.body)
        self.assertNotContains(response, self.community.name)
        self.assertNotContains(response, str(receipt.ack_token))
        context_keys = {
            key for context in response.context for key in context.flatten()
        }
        self.assertFalse({'receipts', 'community', 'participation', 'notice'} & context_keys)
        private_tables = {
            VketParticipation._meta.db_table,
            VketNotice._meta.db_table,
            VketNoticeReceipt._meta.db_table,
            CommunityMember._meta.db_table,
        }
        for table in private_tables:
            self.assertNotIn(table.lower(), sql)

    def test_anonymous_notice_list_is_404_for_draft(self):
        """公開対象pkでも下書きコラボは公開しない"""
        self.collaboration.phase = VketCollaboration.Phase.DRAFT
        self.collaboration.save(update_fields=['phase'])

        response = self.client.get(
            reverse('vket:notice_list', kwargs={'pk': self.collaboration.pk})
        )

        self.assertEqual(response.status_code, 404)

    def test_anonymous_notice_list_has_page_specific_meta(self):
        """公開シェルは採用OGPを絶対URLで出力する"""
        url = reverse('vket:notice_list', kwargs={'pk': self.collaboration.pk})
        response = self.client.get(f'{url}?open={self.notice.pk}')
        image_url = 'http://testserver/static/vket/images/og/vket-2026-summer-notices-v1.png'

        self.assertContains(response, '<meta name="robots" content="noindex,follow">', html=True)
        for name in ('description', 'twitter:title', 'twitter:description', 'twitter:image', 'twitter:image:alt'):
            self.assertContains(response, f'<meta name="{name}"')
        for prop in ('og:title', 'og:description', 'og:image', 'og:image:width', 'og:image:height', 'og:image:alt'):
            self.assertContains(response, f'<meta property="{prop}"')
        self.assertContains(response, image_url, count=2)
        self.assertContains(response, '<meta property="og:image:width" content="1200">', html=True)
        self.assertContains(response, '<meta property="og:image:height" content="630">', html=True)
        self.assertContains(response, f'<link rel="canonical" href="https://vrc-ta-hub.com{url}">', html=True)
        self.assertContains(response, f'<meta property="og:url" content="https://vrc-ta-hub.com{url}">', html=True)

        cdn_url = 'https://cdn.example.com/vket-2026-summer-notices-v1.png'
        with patch('vket.views.notice.static', return_value=cdn_url):
            cdn_response = self.client.get(url)
        self.assertContains(cdn_response, cdn_url, count=2)
        self.assertNotContains(cdn_response, f'/{cdn_url}')

    def test_anonymous_notice_list_preserves_safe_next_for_any_open_value(self):
        """open値は公開内容や機密queryを変えず安全にnextへ保持する"""
        url = reverse('vket:notice_list', kwargs={'pk': self.collaboration.pk})
        values = ('42', '\"><script>alert(1)</script>&receipt=secret')
        query_lists = []
        shell_contents = []

        for value in values:
            with self.subTest(value=value):
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(url, {'open': value})
                full_path = response.wsgi_request.get_full_path()
                login_url = f'{settings.LOGIN_URL}?{urlencode({"next": full_path})}'
                escaped_login_url = escape(login_url, quote=True)
                self.assertContains(response, f'href="{escaped_login_url}"')
                self.assertNotContains(response, '<script>alert(1)</script>')
                query_lists.append([query['sql'] for query in queries.captured_queries])
                shell_contents.append(
                    response.content.decode().replace(escaped_login_url, 'LOGIN_URL')
                )

        self.assertEqual(query_lists[0], query_lists[1])
        self.assertEqual(shell_contents[0], shell_contents[1])

    def test_notice_list_response_is_private_and_varies_on_cookie(self):
        """公開・認証済みの両レスポンスを共有キャッシュへ保存させない"""
        url = reverse('vket:notice_list', kwargs={'pk': self.collaboration.pk})
        responses = [self.client.get(url)]
        self.client.force_login(self.owner)
        responses.append(self.client.get(url))

        for response in responses:
            with self.subTest(authenticated=response.wsgi_request.user.is_authenticated):
                self.assertIn('private', response['Cache-Control'])
                self.assertIn('no-store', response['Cache-Control'])
                self.assertIn('Cookie', response['Vary'])

    def test_notice_list_view_shows_acked_notice_detail(self):
        """参加者側一覧はACK済みでもお知らせ本文を開ける"""
        self.notice.body = '1行目の詳細\n\n"引用" と & 記号を含む本文'
        self.notice.save(update_fields=['body'])
        VketNoticeReceipt.objects.create(
            notice=self.notice,
            participation=self.participation,
            acknowledged_at=timezone.now(),
        )

        self.client.force_login(self.owner)
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
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 403)

    def test_manage_notice_list_shows_notice(self):
        """管理用お知らせ一覧にnoticeが表示される"""
        self.client.force_login(self.superuser)
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

        self.client.force_login(self.superuser)
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

        self.client.force_login(self.owner)
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
        self.client.force_login(self.superuser)
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
        self.client.force_login(self.superuser)
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
        self.client.force_login(self.owner)
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
        self.client.force_login(self.superuser)
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
        self.client.force_login(self.superuser)
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

        self.client.force_login(self.superuser)
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

        self.client.force_login(self.superuser)
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

        self.client.force_login(self.superuser)
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

        self.client.force_login(self.superuser)
        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )
        stat = response.context['notice_stats'][0]
        self.assertIn('<@123456789>', stat['unacked_mentions'])
