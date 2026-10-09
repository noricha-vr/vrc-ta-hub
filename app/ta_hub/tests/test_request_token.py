"""Request-Token 認証の共通 helper のテスト。"""
from django.test import RequestFactory, SimpleTestCase, override_settings

from ta_hub.request_token import is_authorized_request

SERVER_TOKEN = 'server-token'


class IsAuthorizedRequestTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, **headers):
        return self.factory.get('/scheduler/', **headers)

    @override_settings(REQUEST_TOKEN=SERVER_TOKEN)
    def test_matching_token_is_authorized(self):
        self.assertTrue(is_authorized_request(self._request(HTTP_REQUEST_TOKEN=SERVER_TOKEN)))

    @override_settings(REQUEST_TOKEN=SERVER_TOKEN)
    def test_wrong_or_missing_token_is_rejected(self):
        self.assertFalse(is_authorized_request(self._request(HTTP_REQUEST_TOKEN='wrong-token')))
        self.assertFalse(is_authorized_request(self._request()))

    def test_unset_server_token_rejects_everything(self):
        """サーバー側のトークンが空・未設定なら、空のヘッダーでも通さない（fail-closed）。"""
        for server_token in ('', None):
            with self.subTest(server_token=server_token), override_settings(REQUEST_TOKEN=server_token):
                self.assertFalse(is_authorized_request(self._request(HTTP_REQUEST_TOKEN='')))
                self.assertFalse(is_authorized_request(self._request()))
