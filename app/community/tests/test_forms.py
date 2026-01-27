"""CommunityUpdateFormのテスト"""
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
            custom_user=self.user,
            status='approved',
            frequency='毎週',
            organizers='テスト主催者',
            weekdays=['Mon', 'Wed'],
            tags=['tech'],
        )

    def test_notification_webhook_url_in_fields(self):
        """notification_webhook_urlフィールドがフォームに含まれている"""
        form = CommunityUpdateForm(instance=self.community)
        self.assertIn('notification_webhook_url', form.fields)

    def test_notification_webhook_url_widget(self):
        """notification_webhook_urlフィールドのwidgetが正しく設定されている"""
        form = CommunityUpdateForm(instance=self.community)
        widget = form.fields['notification_webhook_url'].widget
        self.assertEqual(widget.attrs.get('class'), 'form-control')
        self.assertIn('discord.com/api/webhooks', widget.attrs.get('placeholder', ''))

    def test_form_saves_notification_webhook_url(self):
        """notification_webhook_urlが正しく保存される"""
        webhook_url = 'https://discord.com/api/webhooks/123/abc'
        form_data = {
            'name': 'テスト集会',
            'start_time': '22:00',
            'duration': 60,
            'frequency': '毎週',
            'organizers': 'テスト主催者',
            'weekdays': ['Mon'],
            'tags': ['tech'],
            'platform': 'All',
            'notification_webhook_url': webhook_url,
        }
        form = CommunityUpdateForm(data=form_data, instance=self.community)
        self.assertTrue(form.is_valid(), form.errors)

        saved_community = form.save()
        self.assertEqual(saved_community.notification_webhook_url, webhook_url)

    def test_notification_webhook_url_is_optional(self):
        """notification_webhook_urlは任意フィールドである"""
        form_data = {
            'name': 'テスト集会',
            'start_time': '22:00',
            'duration': 60,
            'frequency': '毎週',
            'organizers': 'テスト主催者',
            'weekdays': ['Mon'],
            'tags': ['tech'],
            'platform': 'All',
            'notification_webhook_url': '',  # 空でも保存可能
        }
        form = CommunityUpdateForm(data=form_data, instance=self.community)
        self.assertTrue(form.is_valid(), form.errors)

    def test_notification_webhook_url_invalid_url(self):
        """無効なURLはバリデーションエラーになる"""
        form_data = {
            'name': 'テスト集会',
            'start_time': '22:00',
            'duration': 60,
            'frequency': '毎週',
            'organizers': 'テスト主催者',
            'weekdays': ['Mon'],
            'tags': ['tech'],
            'platform': 'All',
            'notification_webhook_url': 'not-a-valid-url',
        }
        form = CommunityUpdateForm(data=form_data, instance=self.community)
        self.assertFalse(form.is_valid())
        self.assertIn('notification_webhook_url', form.errors)
