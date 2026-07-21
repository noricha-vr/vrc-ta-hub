"""api_v1.recurrence_preview の単体テスト

event/tests/test_recurrence_preview_api.py の live smoke と分離し、
RecurrenceService をモック化してバリデーションとエラーハンドリングを
通常の offline suite でカバーする。
"""
from datetime import date, time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from community.models import Community

User = get_user_model()


def _make_user(name="apiuser", email="apiuser@example.com"):
    return User.objects.create_user(user_name=name, email=email, password="testpass123")


def _make_community(name="Test Community"):
    return Community.objects.create(
        name=name,
        start_time=time(22, 0),
        duration=60,
        weekdays=["Mon"],
        frequency="Every week",
        organizers="Test Organizer",
        status="approved",
    )


# 既存テストと同じ URL 推定 (event/tests/test_recurrence_preview_api.py 参照)
# 直接 path で叩くのを避けて DRF view を呼ぶため reverse を試す
def _post_preview(client, payload):
    """recurrence preview API を叩く（URL 名 recurrence-preview / namespace なし）"""
    from django.urls import reverse
    url = reverse("recurrence-preview")
    return client.post(url, data=payload, format="json")


class RecurrencePreviewAuthTest(TestCase):
    """認証必須の検証"""

    def test_unauthenticated_request_is_rejected(self):
        """未認証で 401 / 403 のいずれか"""
        client = APIClient()
        response = _post_preview(client, {"base_date": "2026-01-01"})
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))


class RecurrencePreviewValidationTest(TestCase):
    """post() のバリデーション分岐"""

    def setUp(self):
        self.user = _make_user()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_400_when_base_date_missing(self):
        """base_date 未指定で 400"""
        response = _post_preview(self.client, {"frequency": "WEEKLY"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertIn("基準日", response.data["error"])

    def test_400_when_other_frequency_without_custom_rule(self):
        """frequency=OTHER で custom_rule 空なら 400"""
        response = _post_preview(self.client, {
            "frequency": "OTHER",
            "base_date": "2026-01-01",
            "custom_rule": "",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertIn("カスタムルール", response.data["error"])

    def test_400_when_base_date_format_invalid(self):
        """YYYY-MM-DD 以外の base_date で 400 (ValueError ハンドリング)"""
        response = _post_preview(self.client, {
            "frequency": "WEEKLY",
            "base_date": "2026/01/01",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertIn("日付形式", response.data["error"])

    def test_400_when_base_time_format_invalid(self):
        """HH:MM 以外の base_time で 400"""
        response = _post_preview(self.client, {
            "frequency": "WEEKLY",
            "base_date": "2026-01-01",
            "base_time": "22-00",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])


class RecurrencePreviewSuccessTest(TestCase):
    """正常系の success レスポンス（RecurrenceService をモック）"""

    def setUp(self):
        self.user = _make_user()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("api_v1.recurrence_preview.RecurrenceService.preview_dates")
    def test_returns_success_payload_when_service_succeeds(self, mock_preview):
        """service が success=True を返したらそれを透過"""
        mock_preview.return_value = {
            "success": True,
            "dates": ["2026-01-05", "2026-01-12", "2026-01-19"],
            "count": 3,
        }
        response = _post_preview(self.client, {
            "frequency": "WEEKLY",
            "base_date": "2026-01-01",
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["count"], 3)

    @patch("api_v1.recurrence_preview.RecurrenceService.preview_dates")
    def test_passes_parsed_arguments_to_service(self, mock_preview):
        """service.preview_dates に parse 済み引数が渡される"""
        mock_preview.return_value = {"success": True, "dates": [], "count": 0}
        _post_preview(self.client, {
            "frequency": "WEEKLY",
            "base_date": "2026-02-15",
            "base_time": "20:30",
            "interval": 2,
            "weekday": 0,
            "months": 6,
        })
        kwargs = mock_preview.call_args.kwargs
        self.assertEqual(kwargs["frequency"], "WEEKLY")
        self.assertEqual(kwargs["base_date"], date(2026, 2, 15))
        self.assertEqual(kwargs["base_time"], time(20, 30))
        self.assertEqual(kwargs["interval"], 2)
        self.assertEqual(kwargs["weekday"], 0)
        self.assertEqual(kwargs["months"], 6)


class RecurrencePreviewServiceFailureTest(TestCase):
    """service が success=False を返したケース"""

    def setUp(self):
        self.user = _make_user()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("api_v1.recurrence_preview.RecurrenceService.preview_dates")
    def test_400_when_service_returns_failure(self, mock_preview):
        """success=False を返したら 400 + 元エラーメッセージ"""
        mock_preview.return_value = {
            "success": False,
            "error": "日付生成に失敗しました",
        }
        response = _post_preview(self.client, {
            "frequency": "WEEKLY",
            "base_date": "2026-01-01",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["dates"], [])

    @patch("api_v1.recurrence_preview.RecurrenceService.preview_dates")
    def test_appends_complex_rule_hint_when_error_mentions_complexity(self, mock_preview):
        """エラーメッセージに「複雑」or「解釈」が含まれていればヒント追記"""
        mock_preview.return_value = {
            "success": False,
            "error": "ルールが複雑すぎます",
        }
        response = _post_preview(self.client, {
            "frequency": "WEEKLY",
            "base_date": "2026-01-01",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("複雑", response.data["error"])
        self.assertIn("シンプルなルール", response.data["error"])


class RecurrencePreviewCommunityIdTest(TestCase):
    """community_id 解決の挙動"""

    def setUp(self):
        self.user = _make_user()
        self.community = _make_community()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("api_v1.recurrence_preview.RecurrenceService.preview_dates")
    def test_passes_existing_community_to_service(self, mock_preview):
        """存在する community_id は Community インスタンスに解決して service に渡す"""
        mock_preview.return_value = {"success": True, "dates": [], "count": 0}
        _post_preview(self.client, {
            "frequency": "WEEKLY",
            "base_date": "2026-01-01",
            "community_id": self.community.id,
        })
        kwargs = mock_preview.call_args.kwargs
        self.assertEqual(kwargs["community"], self.community)

    @patch("api_v1.recurrence_preview.RecurrenceService.preview_dates")
    def test_silently_falls_back_when_community_not_found(self, mock_preview):
        """存在しない community_id は silent fallback で community=None を渡す"""
        mock_preview.return_value = {"success": True, "dates": [], "count": 0}
        response = _post_preview(self.client, {
            "frequency": "WEEKLY",
            "base_date": "2026-01-01",
            "community_id": 99999,
        })
        # 200 で community=None として処理される (現実装の意図)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        kwargs = mock_preview.call_args.kwargs
        self.assertIsNone(kwargs["community"])


class RecurrencePreviewUnexpectedExceptionTest(TestCase):
    """予期しない例外で 500"""

    def setUp(self):
        self.user = _make_user()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("api_v1.recurrence_preview.RecurrenceService.preview_dates")
    def test_500_when_service_raises_unexpected_exception(self, mock_preview):
        """RuntimeError などで 500"""
        mock_preview.side_effect = RuntimeError("DB connection lost")
        response = _post_preview(self.client, {
            "frequency": "WEEKLY",
            "base_date": "2026-01-01",
        })
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertFalse(response.data["success"])
        self.assertIn("予期しないエラー", response.data["error"])
