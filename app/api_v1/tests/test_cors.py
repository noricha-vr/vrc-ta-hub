"""公開 API の CORS 許可 Origin を検証する回帰テスト."""

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient


ALLOWED_ORIGIN = 'https://it-infra-meetup.org'
UNKNOWN_ORIGIN = 'https://example.invalid'
PUBLIC_API_URLS = (
    '/api/v1/event/?format=json',
    '/api/v1/event_detail/?format=json',
)


@override_settings(
    CORS_ALLOWED_ORIGINS=[
        'https://vrc-ta-hub.com',
        ALLOWED_ORIGIN,
    ],
)
class PublicApiCorsTests(TestCase):
    """外部連携サイトから公開 API を読めることを検証する."""

    def setUp(self):
        self.client = APIClient()

    def test_public_event_apis_allow_configured_origin_on_get(self):
        """許可 Origin の GET には Access-Control-Allow-Origin を返す."""
        for url in PUBLIC_API_URLS:
            with self.subTest(url=url):
                response = self.client.get(url, HTTP_ORIGIN=ALLOWED_ORIGIN)

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(
                    response.get('access-control-allow-origin'),
                    ALLOWED_ORIGIN,
                )

    def test_public_event_apis_allow_configured_origin_on_preflight(self):
        """許可 Origin の OPTIONS preflight に CORS ヘッダーを返す."""
        for url in PUBLIC_API_URLS:
            with self.subTest(url=url):
                response = self.client.options(
                    url,
                    HTTP_ORIGIN=ALLOWED_ORIGIN,
                    HTTP_ACCESS_CONTROL_REQUEST_METHOD='GET',
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(
                    response.get('access-control-allow-origin'),
                    ALLOWED_ORIGIN,
                )

    def test_public_event_apis_do_not_allow_unknown_origin(self):
        """未許可 Origin には Access-Control-Allow-Origin を返さない."""
        for url in PUBLIC_API_URLS:
            with self.subTest(url=url):
                response = self.client.get(url, HTTP_ORIGIN=UNKNOWN_ORIGIN)

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertIsNone(response.get('access-control-allow-origin'))
