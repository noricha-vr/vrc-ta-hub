"""ログイン方法変更告知の表示テスト."""

from django.test import TestCase, tag
from django.urls import reverse


@tag("offline_external_api")
class LoginMethodChangeNoticeTests(TestCase):
    """ログインページに期限と必要な対応が表示されることを保証する."""

    def test_login_page_shows_login_method_change_notice(self):
        response = self.client.get(reverse("account:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "【重要】ログイン方法変更のお知らせ")
        self.assertContains(response, "2026年8月3日以降、ユーザー名でのログインを廃止します。")
        self.assertContains(response, "メールアドレス")
        self.assertContains(response, "Discord連携")
        self.assertContains(response, "8月3日までに")
        self.assertContains(response, f'href="{reverse("account:settings")}"')
