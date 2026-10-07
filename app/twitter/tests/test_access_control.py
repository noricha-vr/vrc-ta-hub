"""/twitter/ 配下の全ビューの閲覧・操作権限のテスト

5 種類の利用者（未ログイン・他の集会のメンバー・主催者・スタッフ・superuser）について、
各ビューの応答を確認する。
"""

import datetime
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse, reverse_lazy
from django.utils import timezone

from community.models import CommunityMember
from tests.factories import (
    make_community,
    make_community_member,
    make_discord_linked_user,
    make_event,
)
from twitter.models import TweetQueue, TwitterTemplate

CustomUser = get_user_model()

ANONYMOUS = 'anonymous'
OTHER = 'other'
OWNER = 'owner'
STAFF = 'staff'
SUPERUSER = 'superuser'
ALL_ROLES = (ANONYMOUS, OTHER, OWNER, STAFF, SUPERUSER)


class TwitterAccessControlTestBase(TestCase):
    """5 種類の利用者と、集会・イベント・テンプレート・キューを用意する。

    集会は status="pending" で作成し、承認シグナルによる TweetQueue 自動生成を防ぐ。
    """

    def setUp(self):
        self.client = Client()
        self.owner = make_discord_linked_user(
            user_name='acl_owner', email='acl_owner@example.com', discord_uid='9100000001',
        )
        self.staff = make_discord_linked_user(
            user_name='acl_staff', email='acl_staff@example.com', discord_uid='9100000002',
        )
        self.other = make_discord_linked_user(
            user_name='acl_other', email='acl_other@example.com', discord_uid='9100000003',
        )
        self.superuser = make_discord_linked_user(
            user_name='acl_admin', email='acl_admin@example.com', discord_uid='9100000004',
            is_staff=True, is_superuser=True,
        )

        self.community = make_community(name='ACL Community', owner=self.owner, status='pending')
        make_community_member(self.community, self.staff, role=CommunityMember.Role.STAFF)
        self.other_community = make_community(
            name='ACL Other Community', owner=self.other, status='pending',
        )

        self.event = make_event(self.community)
        self.template = TwitterTemplate.objects.create(
            community=self.community, name='ACL Template', template='ACL template body',
        )
        self.other_template = TwitterTemplate.objects.create(
            community=self.other_community, name='ACL Other Template', template='other body',
        )
        self.queue = TweetQueue.objects.create(
            tweet_type='new_community',
            community=self.community,
            generated_text='ACL queue text',
            status='ready',
            scheduled_at=timezone.now() + datetime.timedelta(days=1),
        )

        self.users = {
            OTHER: self.other,
            OWNER: self.owner,
            STAFF: self.staff,
            SUPERUSER: self.superuser,
        }

    def login_as(self, role):
        self.client.logout()
        if role == ANONYMOUS:
            return
        self.client.force_login(self.users[role])
        if role == SUPERUSER:
            # 集会に所属しない superuser にはアクティブ集会が付かない（context processor が消す）
            return
        # テンプレート系ビューはセッションのアクティブ集会を参照する
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

    def assertRedirectsToLogin(self, response):
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith(settings.LOGIN_URL))


@patch('twitter.views.generate_tweet', return_value='generated ACL tweet')
class TweetPreviewAccessTest(TwitterAccessControlTestBase):
    """ポストプレビュー（tweet_event_with_template）"""

    def url(self, template=None):
        return reverse('twitter:tweet_event_with_template', kwargs={
            'event_pk': self.event.pk,
            'template_pk': (template or self.template).pk,
        })

    def test_anonymous_is_redirected_to_login(self, mock_generate):
        self.login_as(ANONYMOUS)
        self.assertRedirectsToLogin(self.client.get(self.url()))
        mock_generate.assert_not_called()

    def test_other_community_user_is_forbidden(self, mock_generate):
        self.login_as(OTHER)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        mock_generate.assert_not_called()

    def test_owner_staff_and_superuser_can_view(self, mock_generate):
        for role in (OWNER, STAFF, SUPERUSER):
            with self.subTest(role=role):
                self.login_as(role)
                response = self.client.get(self.url())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['raw_tweet_text'], 'generated ACL tweet')

    def test_template_of_another_community_is_not_found(self, mock_generate):
        self.login_as(OWNER)
        self.assertEqual(self.client.get(self.url(self.other_template)).status_code, 404)
        mock_generate.assert_not_called()

    def test_missing_event_is_not_found_for_logged_in_user(self, mock_generate):
        self.login_as(OWNER)
        url = reverse('twitter:tweet_event_with_template', kwargs={
            'event_pk': self.event.pk + 10000, 'template_pk': self.template.pk,
        })
        self.assertEqual(self.client.get(url).status_code, 404)


class TemplateListAccessTest(TwitterAccessControlTestBase):
    """テンプレート一覧（template_list）: 所属する集会のテンプレート、superuser は全部"""

    url = reverse_lazy('twitter:template_list')

    def test_get_by_role(self):
        # (自分の集会のテンプレートが見えるか, 他の集会のテンプレートが見えるか)
        expected = {
            OTHER: (False, True), OWNER: (True, False), STAFF: (True, False), SUPERUSER: (True, True),
        }

        self.login_as(ANONYMOUS)
        self.assertRedirectsToLogin(self.client.get(self.url))

        for role, (sees_template, sees_other) in expected.items():
            with self.subTest(role=role):
                self.login_as(role)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.template in response.context['templates'], sees_template)
                self.assertEqual(self.other_template in response.context['templates'], sees_other)

    def test_post_is_not_allowed(self):
        self.login_as(ANONYMOUS)
        self.assertRedirectsToLogin(self.client.post(self.url))
        for role in (OTHER, OWNER, STAFF, SUPERUSER):
            with self.subTest(role=role):
                self.login_as(role)
                self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_create_button_only_for_managed_active_community(self):
        create_url = reverse('twitter:template_create')
        for role, shown in ((OWNER, True), (STAFF, True), (SUPERUSER, False)):
            with self.subTest(role=role):
                self.login_as(role)
                html = self.client.get(self.url).content.decode()
                self.assertEqual(f'{create_url}?community={self.community.pk}' in html, shown)


class TemplateCreateAccessTest(TwitterAccessControlTestBase):
    """テンプレート作成（template_create）: 集会の主催者・スタッフと superuser"""

    def url(self, community=None):
        return f"{reverse('twitter:template_create')}?community={(community or self.community).pk}"

    def test_get_by_role(self):
        expected_status = {OTHER: 403, OWNER: 200, STAFF: 200, SUPERUSER: 200}

        self.login_as(ANONYMOUS)
        self.assertRedirectsToLogin(self.client.get(self.url()))

        for role, status in expected_status.items():
            with self.subTest(role=role):
                self.login_as(role)
                self.assertEqual(self.client.get(self.url()).status_code, status)

    def test_post_by_role(self):
        expected_created = {
            ANONYMOUS: False, OTHER: False, OWNER: True, STAFF: True, SUPERUSER: True,
        }
        for role, created in expected_created.items():
            with self.subTest(role=role):
                name = f'created-by-{role}'
                self.login_as(role)
                response = self.client.post(self.url(), {'name': name, 'template': 'body'})
                if role == ANONYMOUS:
                    self.assertRedirectsToLogin(response)
                elif role == OTHER:
                    self.assertEqual(response.status_code, 403)
                else:
                    self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    TwitterTemplate.objects.filter(name=name, community=self.community).exists(),
                    created,
                )

    def test_session_active_community_is_used_without_query(self):
        self.login_as(STAFF)
        response = self.client.post(
            reverse('twitter:template_create'), {'name': 'from-session', 'template': 'body'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            TwitterTemplate.objects.filter(name='from-session', community=self.community).exists(),
        )

    def test_superuser_without_community_is_forbidden(self):
        """作る先の集会が決まらない時は作れない."""
        self.login_as(SUPERUSER)
        self.assertEqual(self.client.get(reverse('twitter:template_create')).status_code, 403)

    def test_member_cannot_create_for_another_community_by_query(self):
        self.login_as(STAFF)
        response = self.client.post(
            self.url(self.other_community), {'name': 'cross', 'template': 'body'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(TwitterTemplate.objects.filter(name='cross').exists())


class TemplateUpdateAccessTest(TwitterAccessControlTestBase):
    """テンプレート編集（template_update）: 集会の主催者・スタッフと superuser"""

    def url(self):
        return reverse('twitter:template_update', kwargs={'pk': self.template.pk})

    def test_get_by_role(self):
        expected_status = {OTHER: 403, OWNER: 200, STAFF: 200, SUPERUSER: 200}

        self.login_as(ANONYMOUS)
        self.assertRedirectsToLogin(self.client.get(self.url()))

        for role, status in expected_status.items():
            with self.subTest(role=role):
                self.login_as(role)
                self.assertEqual(self.client.get(self.url()).status_code, status)

    def test_post_by_role(self):
        for role in ALL_ROLES:
            with self.subTest(role=role):
                name = f'updated-by-{role}'
                self.login_as(role)
                response = self.client.post(self.url(), {'name': name, 'template': 'body'})
                self.template.refresh_from_db()
                if role == ANONYMOUS:
                    self.assertRedirectsToLogin(response)
                    self.assertNotEqual(self.template.name, name)
                elif role == OTHER:
                    self.assertEqual(response.status_code, 403)
                    self.assertNotEqual(self.template.name, name)
                else:
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(self.template.name, name)
                self.assertEqual(self.template.community, self.community)

    def test_update_keeps_community_even_if_active_community_differs(self):
        """2 つの集会のメンバーが、別の集会を選んだまま編集しても付け替わらない."""
        make_community_member(self.other_community, self.staff, role=CommunityMember.Role.STAFF)
        self.login_as(STAFF)
        session = self.client.session
        session['active_community_id'] = self.other_community.id
        session.save()
        response = self.client.post(self.url(), {'name': 'kept', 'template': 'body'})
        self.assertEqual(response.status_code, 302)
        self.template.refresh_from_db()
        self.assertEqual(self.template.community, self.community)


class TemplateDeleteAccessTest(TwitterAccessControlTestBase):
    """テンプレート削除（template_delete）: 集会の主催者・スタッフと superuser"""

    def test_access_by_role(self):
        expected_deleted = {
            ANONYMOUS: False, OTHER: False, OWNER: True, STAFF: True, SUPERUSER: True,
        }
        for role in ALL_ROLES:
            with self.subTest(role=role):
                template = TwitterTemplate.objects.create(
                    community=self.community, name=f'delete-{role}', template='body',
                )
                self.login_as(role)
                response = self.client.post(
                    reverse('twitter:template_delete', kwargs={'pk': template.pk}),
                )
                deleted = not TwitterTemplate.objects.filter(pk=template.pk).exists()
                self.assertEqual(deleted, expected_deleted[role])
                if role == ANONYMOUS:
                    self.assertRedirectsToLogin(response)
                elif role == OTHER:
                    self.assertEqual(response.status_code, 403)


class TweetQueueListAccessTest(TwitterAccessControlTestBase):
    """キュー一覧（tweet_queue_list）: 所属する集会のキューだけが見える"""

    def test_access_by_role(self):
        url = reverse('twitter:tweet_queue_list')
        expected_visible = {OTHER: False, OWNER: True, STAFF: True, SUPERUSER: True}

        self.login_as(ANONYMOUS)
        self.assertRedirectsToLogin(self.client.get(url))

        for role, visible in expected_visible.items():
            with self.subTest(role=role):
                self.login_as(role)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.queue in response.context['tweet_queues'], visible)


class TweetQueueDetailAccessTest(TwitterAccessControlTestBase):
    """キュー詳細（tweet_queue_detail）: 閲覧は所属集会のみ、編集は superuser のみ"""

    def url(self):
        return reverse('twitter:tweet_queue_detail', kwargs={'pk': self.queue.pk})

    def test_get_by_role(self):
        expected_status = {OTHER: 404, OWNER: 200, STAFF: 200, SUPERUSER: 200}

        self.login_as(ANONYMOUS)
        self.assertRedirectsToLogin(self.client.get(self.url()))

        for role, status in expected_status.items():
            with self.subTest(role=role):
                self.login_as(role)
                self.assertEqual(self.client.get(self.url()).status_code, status)

    @patch('twitter.views._post_tweet_queue_item')
    def test_post_by_role(self, mock_post):
        expected_status = {OTHER: 403, OWNER: 403, STAFF: 403}

        self.login_as(ANONYMOUS)
        self.assertRedirectsToLogin(self.client.post(self.url(), {'action': 'post_now'}))

        for role, status in expected_status.items():
            with self.subTest(role=role):
                self.login_as(role)
                response = self.client.post(self.url(), {'action': 'post_now'})
                self.assertEqual(response.status_code, status)
        mock_post.assert_not_called()

        self.login_as(SUPERUSER)
        response = self.client.post(self.url(), {
            'action': 'update',
            'generated_text': 'updated by admin',
            'image_url': '',
            'scheduled_at': '',
        })
        self.assertEqual(response.status_code, 302)
        self.queue.refresh_from_db()
        self.assertEqual(self.queue.generated_text, 'updated by admin')


class PostScheduledTweetsAccessTest(TwitterAccessControlTestBase):
    """予約投稿の起動口（post_scheduled_tweets）: トークンでのみ認可する"""

    @patch('twitter.views.process_scheduled_tweets', return_value={'ok': True})
    def test_rejects_when_server_token_is_unset(self, mock_process):
        url = reverse('twitter:post_scheduled_tweets')
        with patch.dict('os.environ', {'REQUEST_TOKEN': ''}):
            for role in ALL_ROLES:
                with self.subTest(role=role):
                    self.login_as(role)
                    self.assertEqual(self.client.get(url).status_code, 401)
                    self.assertEqual(
                        self.client.get(url, HTTP_REQUEST_TOKEN='').status_code, 401,
                    )
        mock_process.assert_not_called()

    @patch('twitter.views.process_scheduled_tweets', return_value={'ok': True})
    def test_login_alone_does_not_authorize(self, mock_process):
        url = reverse('twitter:post_scheduled_tweets')
        with patch.dict('os.environ', {'REQUEST_TOKEN': 'acl-token'}):
            for role in ALL_ROLES:
                with self.subTest(role=role):
                    self.login_as(role)
                    self.assertEqual(self.client.get(url).status_code, 401)
            self.login_as(ANONYMOUS)
            response = self.client.get(url, HTTP_REQUEST_TOKEN='acl-token')
            self.assertEqual(response.status_code, 200)
        mock_process.assert_called_once()
