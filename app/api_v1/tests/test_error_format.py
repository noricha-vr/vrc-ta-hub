"""API v1 のエラーレスポンス形式テスト（Issue #538）

契約:
  - すべてのエラーレスポンスが機械可読な ``code`` を持つ
  - ``detail`` を持ち、日本語キー（"エラー" 等）を使わない
  - 内部例外の生文字列（str(exc)）をレスポンスに載せない
"""
from datetime import date, time, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from rest_framework.exceptions import NotFound, ValidationError

from api_v1.exception_handler import api_exception_handler
from community.models import Community, CommunityMember
from event.models import Event, EventDetail
from user_account.models import APIKey, CustomUser


class ExceptionHandlerTest(TestCase):
    """共通例外ハンドラの code / detail 決定ロジック"""

    def _handle(self, exc):
        return api_exception_handler(exc, {})

    def test_field_validation_error_uses_unified_validation_code(self):
        """フィールド別 ValidationError も top-level は validation_error に統一"""
        response = self._handle(ValidationError({'start_time': ['開始時刻が不正です。']}))

        self.assertEqual(response.data['code'], 'validation_error')

    def test_non_field_validation_error_uses_unified_validation_code(self):
        """非フィールドの ValidationError（リスト形式）も同じ code"""
        response = self._handle(ValidationError(['入力が不正です。']))

        self.assertEqual(response.data['code'], 'validation_error')

    def test_field_validation_detail_uses_field_message_not_english_default(self):
        """detail は英語 default_detail ではなくフィールドの日本語メッセージを使う"""
        response = self._handle(ValidationError({'start_time': ['開始時刻が不正です。']}))

        self.assertEqual(response.data['detail'], '開始時刻が不正です。')
        self.assertEqual(response.data['start_time'], ['開始時刻が不正です。'])

    def test_field_validation_detail_ignores_injected_code_value(self):
        """detail 決定に、追記した code の値を拾わない"""
        response = self._handle(ValidationError({'event': ['イベントが存在しません。']}))

        self.assertEqual(response.data['detail'], 'イベントが存在しません。')

    def test_non_validation_error_keeps_drf_code(self):
        """ValidationError 以外は DRF の code をそのまま使う"""
        response = self._handle(NotFound())

        self.assertEqual(response.data['code'], 'not_found')


class APIKeyAuthErrorFormatTest(TestCase):
    """認証失敗時のエラー形式（api_v1.authentication）"""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            user_name='auth_user', email='auth@example.com', password='testpass123'
        )
        self.client = APIClient()
        self.url = reverse('event-detail-api-list')

    def _get_with_key(self, raw_key):
        return self.client.get(self.url, HTTP_AUTHORIZATION=f'Bearer {raw_key}')

    # WWW-Authenticate ヘッダを返さない認証方式のため DRF は 401 を 403 に変換する。
    AUTH_FAILED_STATUSES = (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_unknown_api_key_returns_detail_and_code(self):
        """存在しないキーは detail + code=invalid_api_key"""
        response = self._get_with_key('this-key-does-not-exist')

        self.assertIn(response.status_code, self.AUTH_FAILED_STATUSES)
        self.assertEqual(response.data['code'], 'invalid_api_key')
        self.assertIn('detail', response.data)

    def test_expired_api_key_is_indistinguishable_from_unknown_key(self):
        """期限切れキーも同一の detail / code（キー実在の推測を防ぐ）"""
        api_key, raw_key = APIKey.create_with_raw_key(user=self.user, name='expired')
        api_key.expires_at = timezone.now() - timedelta(days=1)
        api_key.save(update_fields=['expires_at'])

        expired = self._get_with_key(raw_key)
        unknown = self._get_with_key('this-key-does-not-exist')

        self.assertIn(expired.status_code, self.AUTH_FAILED_STATUSES)
        self.assertEqual(expired.status_code, unknown.status_code)
        self.assertEqual(expired.data['code'], 'invalid_api_key')
        self.assertEqual(expired.data['detail'], unknown.data['detail'])
        self.assertEqual(expired.data['code'], unknown.data['code'])

    def test_ip_denied_api_key_is_indistinguishable_from_unknown_key(self):
        """IP 許可リスト不一致も同一の detail / code"""
        api_key, raw_key = APIKey.create_with_raw_key(user=self.user, name='ip')
        api_key.allowed_ips = '203.0.113.0/24'
        api_key.save(update_fields=['allowed_ips'])

        denied = self._get_with_key(raw_key)

        self.assertIn(denied.status_code, self.AUTH_FAILED_STATUSES)
        self.assertEqual(denied.data['code'], 'invalid_api_key')

    def test_unauthenticated_request_has_code(self):
        """認証情報なしの DRF 標準エラーにも code が載る"""
        response = self.client.get(self.url)

        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.assertIn('code', response.data)
        self.assertIn('detail', response.data)


class EventDetailPermissionErrorFormatTest(TestCase):
    """権限エラーのレスポンス形式（日本語キーの解消）"""

    def setUp(self):
        self.owner = CustomUser.objects.create_user(
            user_name='owner_user', email='owner@example.com', password='testpass123'
        )
        _, self.owner_raw_key = APIKey.create_with_raw_key(user=self.owner, name='owner')

        self.community = Community.objects.create(
            name='Error Format Community',
            start_time=time(20, 0),
            duration=120,
            weekdays='mon',
            frequency='weekly',
            organizers='Organizer',
            status='approved',
        )
        CommunityMember.objects.create(
            community=self.community, user=self.owner, role=CommunityMember.Role.OWNER
        )
        event = Event.objects.create(
            community=self.community,
            date=date(2024, 12, 25),
            start_time=time(20, 0),
            duration=120,
            weekday='wed',
        )
        self.event_detail = EventDetail.objects.create(
            event=event,
            detail_type='LT',
            start_time=time(20, 0),
            duration=30,
            speaker='Speaker',
            theme='Theme',
            h1='Title',
            contents='contents',
        )
        self.client = APIClient()

    @patch('community.models.Community.is_manager', return_value=False)
    def test_destroy_without_permission_returns_detail_and_code(self, _mock_is_manager):
        """権限なし削除は日本語キーではなく detail + code=permission_denied

        get_queryset は所属コミュニティで絞るため、管理権限だけ失った状態を
        is_manager のモックで再現する。
        """
        url = reverse('event-detail-api-detail', kwargs={'pk': self.event_detail.id})
        response = self.client.delete(url, HTTP_AUTHORIZATION=f'Bearer {self.owner_raw_key}')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotIn('エラー', response.data)
        self.assertEqual(response.data['code'], 'permission_denied')
        self.assertIn('detail', response.data)


class RecurrencePreviewErrorFormatTest(TestCase):
    """recurrence-preview のエラー形式（str(exc) 露出の解消）"""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            user_name='preview_user', email='preview@example.com', password='testpass123'
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('recurrence-preview')

    def test_validation_error_has_code(self):
        """入力不正は既存フィールドを維持しつつ code=validation_error"""
        response = self.client.post(self.url, data={}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])  # 既存クライアント互換
        self.assertEqual(response.data['code'], 'validation_error')
        self.assertIn('detail', response.data)

    @patch('event.recurrence_service.RecurrenceService.preview_dates')
    def test_service_failure_has_code(self, mock_preview):
        """サービス側の失敗は code=preview_failed"""
        mock_preview.return_value = {'success': False, 'error': '日付生成に失敗しました'}

        response = self.client.post(
            self.url, data={'base_date': '2026-01-01', 'frequency': '毎週'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['code'], 'preview_failed')

    @patch('event.recurrence_service.RecurrenceService.preview_dates')
    def test_unexpected_exception_does_not_leak_exception_text(self, mock_preview):
        """予期しない例外で str(exc) をレスポンスに載せない"""
        mock_preview.side_effect = RuntimeError('SECRET-INTERNAL-DETAIL')

        response = self.client.post(
            self.url, data={'base_date': '2026-01-01', 'frequency': '毎週'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(response.data['code'], 'internal_error')
        self.assertNotIn('SECRET-INTERNAL-DETAIL', str(response.data))
        self.assertNotIn('SECRET-INTERNAL-DETAIL', response.data['error'])
