import re

from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from user_account.models import APIKey
from user_account.tests.utils import create_discord_linked_user


RAW_KEY_RE = re.compile(r"^[A-Za-z0-9]{64}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class APIKeyViewsSecurityTests(TestCase):
    """APIキーの作成/表示まわりのセキュリティ要件をテストする。"""

    def setUp(self):
        self.client = Client()
        self.user = create_discord_linked_user(
            user_name="test_user",
            email="test_user@example.com",
            password="testpass123",
        )

        self.list_url = reverse("account:api_key_list")
        self.create_url = reverse("account:api_key_create")

    def test_create_api_key_stores_hash_and_shows_raw_key_only_once(self):
        """作成直後のみ平文キーが表示され、DBにはハッシュのみが保存されること。"""
        self.client.login(username="test_user", password="testpass123")

        response = self.client.post(
            self.create_url,
            {"name": "My API Key"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/api_key_list.html")

        raw_key = response.context["new_api_key"]
        self.assertIsNotNone(raw_key)
        self.assertTrue(RAW_KEY_RE.match(raw_key))

        # DBには平文ではなくSHA-256(hex)が保存される
        api_key_obj = APIKey.objects.get(user=self.user, is_active=True)
        self.assertTrue(HEX64_RE.match(api_key_obj.key))
        self.assertNotEqual(api_key_obj.key, raw_key)
        self.assertEqual(api_key_obj.key, APIKey.hash_raw_key(raw_key))

        # messages には平文が含まれない（cookie経由の漏洩を防ぐ）
        for msg in get_messages(response.wsgi_request):
            self.assertNotIn(raw_key, str(msg))

        # 2回目以降は表示されない（セッションからpopされる）
        self.assertIsNone(self.client.session.get("new_api_key"))
        response2 = self.client.get(self.list_url)
        self.assertEqual(response2.status_code, 200)
        self.assertIsNone(response2.context["new_api_key"])
        self.assertNotContains(response2, raw_key)

