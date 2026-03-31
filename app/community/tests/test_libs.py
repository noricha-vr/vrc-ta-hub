"""community.libs のテスト。"""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from community.libs import (
    VRCHAT_BASE_URL,
    get_join_type,
    resolve_vrc_group_url,
)


class ResolveVrcGroupUrlTest(TestCase):
    """resolve_vrc_group_url のテスト。"""

    def test_empty_string_returns_empty(self):
        """空文字の場合はそのまま返す。"""
        self.assertEqual(resolve_vrc_group_url(""), "")

    def test_none_returns_none(self):
        """None の場合はそのまま返す。"""
        self.assertIsNone(resolve_vrc_group_url(None))

    def test_non_vrc_group_url_unchanged(self):
        """vrc.group ドメインでないURLはそのまま返す。"""
        url = "https://vrchat.com/home/group/grp_6059c01a-7923-4e6b-a303-0151a753deeb"
        self.assertEqual(resolve_vrc_group_url(url), url)

    def test_other_domain_unchanged(self):
        """他のドメインのURLはそのまま返す。"""
        url = "https://example.com/VRCTS.5197"
        self.assertEqual(resolve_vrc_group_url(url), url)

    def test_vrchat_com_url_unchanged(self):
        """vrchat.com の正規URLはそのまま返す。"""
        url = "https://vrchat.com/home/group/grp_abcdefgh-1234-5678-abcd-123456789abc"
        self.assertEqual(resolve_vrc_group_url(url), url)

    @patch("community.libs.requests.head")
    def test_short_url_resolved_successfully(self, mock_head):
        """短縮URLの解決に成功したケース。"""
        location = "/home/group/grp_6059c01a-7923-4e6b-a303-0151a753deeb"
        mock_response = MagicMock()
        mock_response.headers = {"Location": location}
        mock_head.return_value = mock_response

        result = resolve_vrc_group_url("https://vrc.group/VRCTS.5197")

        self.assertEqual(result, f"{VRCHAT_BASE_URL}{location}")
        mock_head.assert_called_once()
        call_args = mock_head.call_args
        self.assertIn("VRCTS.5197", call_args[0][0])
        self.assertEqual(call_args[1]["allow_redirects"], False)

    @patch("community.libs.requests.head")
    def test_short_url_timeout_returns_original(self, mock_head):
        """タイムアウト時は元のURLを返す。"""
        import requests

        mock_head.side_effect = requests.Timeout("Connection timed out")

        original_url = "https://vrc.group/VRCTS.5197"
        result = resolve_vrc_group_url(original_url)
        self.assertEqual(result, original_url)

    @patch("community.libs.requests.head")
    def test_short_url_connection_error_returns_original(self, mock_head):
        """接続エラー時は元のURLを返す。"""
        import requests

        mock_head.side_effect = requests.ConnectionError("Connection refused")

        original_url = "https://vrc.group/VRCTS.5197"
        result = resolve_vrc_group_url(original_url)
        self.assertEqual(result, original_url)

    @patch("community.libs.requests.head")
    def test_no_location_header_returns_original(self, mock_head):
        """Location ヘッダーがない場合は元のURLを返す。"""
        mock_response = MagicMock()
        mock_response.headers = {}
        mock_head.return_value = mock_response

        original_url = "https://vrc.group/VRCTS.5197"
        result = resolve_vrc_group_url(original_url)
        self.assertEqual(result, original_url)

    @patch("community.libs.requests.head")
    def test_unexpected_location_returns_original(self, mock_head):
        """Location に /home/group/ が含まれない場合は元のURLを返す。"""
        mock_response = MagicMock()
        mock_response.headers = {"Location": "/some/other/path"}
        mock_head.return_value = mock_response

        original_url = "https://vrc.group/VRCTS.5197"
        result = resolve_vrc_group_url(original_url)
        self.assertEqual(result, original_url)

    def test_vrc_group_url_with_no_path_returns_original(self):
        """パスがない vrc.group URL はそのまま返す。"""
        url = "https://vrc.group/"
        self.assertEqual(resolve_vrc_group_url(url), url)

    @patch("community.libs.requests.head")
    def test_user_agent_header_sent(self, mock_head):
        """User-Agent ヘッダーが送信される。"""
        mock_response = MagicMock()
        mock_response.headers = {
            "Location": "/home/group/grp_test"
        }
        mock_head.return_value = mock_response

        resolve_vrc_group_url("https://vrc.group/TEST.1234")

        call_kwargs = mock_head.call_args[1]
        self.assertEqual(
            call_kwargs["headers"]["User-Agent"],
            "vrc-ta-hub/1.0 noricha-vr",
        )

    @patch("community.libs.requests.head")
    def test_timeout_is_set(self, mock_head):
        """タイムアウトが5秒に設定される。"""
        mock_response = MagicMock()
        mock_response.headers = {
            "Location": "/home/group/grp_test"
        }
        mock_head.return_value = mock_response

        resolve_vrc_group_url("https://vrc.group/TEST.1234")

        call_kwargs = mock_head.call_args[1]
        self.assertEqual(call_kwargs["timeout"], 5)


class GetJoinTypeTest(TestCase):
    """get_join_type のテスト（既存関数の回帰テスト）。"""

    def test_group_type(self):
        self.assertEqual(get_join_type("https://vrchat.com/home/group/grp_xxx"), "group")

    def test_user_page_type(self):
        self.assertEqual(get_join_type("/custom_user/abc"), "user_page")

    def test_world_type(self):
        self.assertEqual(get_join_type("https://vrch.at/xxx"), "world")

    def test_user_name_type(self):
        self.assertEqual(get_join_type("some_user"), "user_name")
