"""CommunityUpdateFormのテスト"""
from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth import get_user_model

from community.models import Community
from community.forms import CommunityUpdateForm

CustomUser = get_user_model()


class CommunityUpdateFormTest(TestCase):
    """CommunityUpdateFormのテスト"""

    def setUp(self):
        # テスト用ユーザー
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='testpass123',
            user_name='テストユーザー'
        )

        # テスト用集会
        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週',
            organizers='テスト主催者',
            weekdays=['Mon', 'Wed'],
            tags=['tech'],
        )

    def test_notification_webhook_url_not_in_fields(self):
        """notification_webhook_urlフィールドがフォームに含まれていない(settings.htmlで管理)"""
        form = CommunityUpdateForm(instance=self.community)
        self.assertNotIn('notification_webhook_url', form.fields)

    def test_form_saves_without_notification_webhook_url(self):
        """notification_webhook_urlなしで正常に保存できる"""
        form_data = {
            'name': 'テスト集会',
            'start_time': '22:00',
            'duration': 60,
            'frequency': '毎週',
            'organizers': 'テスト主催者',
            'weekdays': ['Mon'],
            'tags': ['tech'],
            'platform': 'All',
        }
        form = CommunityUpdateForm(data=form_data, instance=self.community)
        self.assertTrue(form.is_valid(), form.errors)

        saved_community = form.save()
        self.assertEqual(saved_community.name, 'テスト集会')

    def test_required_fields(self):
        """必須フィールドのテスト"""
        form = CommunityUpdateForm(instance=self.community)
        expected_fields = [
            'name', 'start_time', 'duration', 'weekdays', 'frequency', 'organizers',
            'group_url', 'organizer_url', 'sns_url', 'discord', 'twitter_hashtag',
            'poster_image', 'allow_poster_repost', 'description', 'platform', 'tags',
        ]
        for field in expected_fields:
            self.assertIn(field, form.fields, f'{field} should be in form fields')

    def test_accepts_lt_application_not_in_form(self):
        """accepts_lt_applicationフィールドがフォームに含まれていない(settings.htmlで管理)"""
        form = CommunityUpdateForm(instance=self.community)
        self.assertNotIn('accepts_lt_application', form.fields)

    def test_group_url_rejects_non_vrchat_domain(self):
        """VRChat以外のドメインのURLは拒否される。"""
        form_data = {
            'name': 'テスト集会',
            'start_time': '22:00',
            'duration': 60,
            'frequency': '毎週',
            'organizers': 'テスト主催者',
            'weekdays': ['Mon'],
            'tags': ['tech'],
            'platform': 'All',
            'group_url': 'https://t.co/abc123',
        }
        form = CommunityUpdateForm(data=form_data, instance=self.community)
        self.assertFalse(form.is_valid())
        self.assertIn('group_url', form.errors)

    def test_group_url_accepts_vrchat_com(self):
        """vrchat.com のURLは受け付ける。"""
        form_data = {
            'name': 'テスト集会',
            'start_time': '22:00',
            'duration': 60,
            'frequency': '毎週',
            'organizers': 'テスト主催者',
            'weekdays': ['Mon'],
            'tags': ['tech'],
            'platform': 'All',
            'group_url': 'https://vrchat.com/home/group/grp_test-1234',
        }
        form = CommunityUpdateForm(data=form_data, instance=self.community)
        self.assertTrue(form.is_valid(), form.errors)

    @patch('community.forms.resolve_vrc_group_url')
    def test_group_url_converts_short_url(self, mock_resolve):
        """vrc.group 短縮URLが正規URLに変換される。"""
        mock_resolve.return_value = 'https://vrchat.com/home/group/grp_resolved-id'
        form_data = {
            'name': 'テスト集会',
            'start_time': '22:00',
            'duration': 60,
            'frequency': '毎週',
            'organizers': 'テスト主催者',
            'weekdays': ['Mon'],
            'tags': ['tech'],
            'platform': 'All',
            'group_url': 'https://vrc.group/TEST.1234',
        }
        form = CommunityUpdateForm(data=form_data, instance=self.community)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['group_url'], 'https://vrchat.com/home/group/grp_resolved-id')
