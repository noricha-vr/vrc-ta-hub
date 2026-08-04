"""認証画面間のnext引き継ぎを検証する."""

from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, override_settings, tag
from django.urls import reverse

from allauth.account.models import EmailAddress
from allauth.core.context import request_context
from allauth.socialaccount.internal.flows.signup import process_signup
from allauth.socialaccount.models import SocialAccount, SocialLogin

from user_account.tests.utils import (
    TEST_SOCIALACCOUNT_PROVIDERS,
    TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS,
)
from tests.factories import make_community, make_user


User = get_user_model()


class HrefCollector(HTMLParser):
    """レンダリング済みHTMLからリンク先を収集する."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag != 'a':
            return
        href = dict(attrs).get('href')
        if href:
            self.hrefs.append(href)


def collect_hrefs(response: HttpResponse) -> list[str]:
    """レスポンス内のリンク先を返す."""
    parser = HrefCollector()
    parser.feed(response.content.decode())
    return parser.hrefs


def has_next_link(hrefs: list[str], path: str, next_url: str) -> bool:
    """指定画面へのリンクがnextを保持するか判定する."""
    return any(
        urlparse(href).path == path
        and parse_qs(urlparse(href).query).get('next') == [next_url]
        for href in hrefs
    )


def discord_login_href(hrefs: list[str]) -> str:
    """Discordログインリンクを返す."""
    for href in hrefs:
        if urlparse(href).path.endswith('/discord/login/'):
            return href
    raise AssertionError('Discordログインリンクが見つかりません')


class DuplicateEmailForm(forms.Form):
    """メール重複エラー表示用の最小フォーム."""

    user_name = forms.CharField()
    email = forms.EmailField()


def render_duplicate_email_signup(redirect_url: str, request_next: str = '') -> HttpResponse:
    """メール重複状態のソーシャル登録画面を描画する."""
    form = DuplicateEmailForm(data={
        'user_name': 'new_user',
        'email': 'existing@example.com',
    })
    if form.is_valid():
        form.add_error('email', 'このメールアドレスは既に登録されています')
    html = render_to_string('socialaccount/signup.html', {
        'form': form,
        'redirect_field_name': 'next',
        'redirect_field_value': redirect_url,
        'request': SimpleNamespace(GET={'next': request_next}),
    })
    return HttpResponse(html)


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS)
@tag('offline_external_api')
class AuthNextLinkTests(TestCase):
    """ログイン・登録・Discord導線のnext引き継ぎを検証する."""

    def setUp(self) -> None:
        self.client = Client()
        self.login_url = reverse('account:login')
        self.register_url = reverse('account:register')
        self.next_url = '/event/speaker-link/signed-token/'

    def test_login_page_preserves_safe_next_in_links_and_form(self) -> None:
        """ログイン画面の登録・Discord・POST導線がnextを引き継ぐこと."""
        response = self.client.get(self.login_url, {'next': self.next_url})
        hrefs = collect_hrefs(response)

        self.assertTrue(has_next_link(hrefs, self.register_url, self.next_url))
        discord_url = urlparse(discord_login_href(hrefs))
        self.assertEqual(parse_qs(discord_url.query)['next'], [self.next_url])
        self.assertContains(response, f'name="next" value="{self.next_url}"')

    def test_register_page_preserves_safe_next_in_links(self) -> None:
        """登録画面のログイン・Discord導線がnextを引き継ぐこと."""
        response = self.client.get(self.register_url, {'next': self.next_url})
        hrefs = collect_hrefs(response)

        self.assertTrue(has_next_link(hrefs, self.login_url, self.next_url))
        discord_url = urlparse(discord_login_href(hrefs))
        self.assertEqual(parse_qs(discord_url.query)['next'], [self.next_url])

    def test_auth_links_do_not_render_external_next(self) -> None:
        """ログイン・登録画面の導線が外部nextを引き継がないこと."""
        external_url = 'https://evil.example.com/path'

        for page_url in (self.login_url, self.register_url):
            with self.subTest(page_url=page_url):
                response = self.client.get(page_url, {'next': external_url})
                hrefs = collect_hrefs(response)

                self.assertNotContains(response, 'evil.example.com')
                self.assertFalse(any(
                    parse_qs(urlparse(href).query).get('next') == [external_url]
                    for href in hrefs
                ))


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS)
@tag('offline_external_api')
class LocalSignupNextTests(TestCase):
    """ローカル登録後のログイン復帰先を検証する."""

    def setUp(self) -> None:
        self.client = Client()
        self.register_url = reverse('account:register')
        self.login_url = reverse('account:login')

    def test_local_signup_preserves_safe_next_on_login_redirect(self) -> None:
        """ローカル登録フォームと登録後ログインURLがnextを保持すること."""
        next_url = '/event/speaker-link/signed-token/'
        page_response = self.client.get(self.register_url, {'next': next_url})
        self.assertContains(page_response, f'name="next" value="{next_url}"')

        response = self.client.post(self.register_url, {
            'user_name': 'local_signup_with_next',
            'email': 'local-signup-with-next@example.com',
            'password1': 'testpass12345',
            'password2': 'testpass12345',
            'next': next_url,
        })

        redirect_url = urlparse(response.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(redirect_url.path, self.login_url)
        self.assertEqual(parse_qs(redirect_url.query)['next'], [next_url])

    def test_local_signup_rejects_external_next_on_login_redirect(self) -> None:
        """ローカル登録後ログインURLが外部nextを保持しないこと."""
        response = self.client.post(self.register_url, {
            'user_name': 'local_signup_external_next',
            'email': 'local-signup-external-next@example.com',
            'password1': 'testpass12345',
            'password2': 'testpass12345',
            'next': 'https://evil.example.com/path',
        })

        self.assertRedirects(
            response,
            self.login_url,
            fetch_redirect_response=False,
        )


@tag('offline_external_api')
class SocialSignupDuplicateEmailNextTests(TestCase):
    """Discord登録のメール重複時ログイン導線を検証する."""

    def test_duplicate_email_login_link_preserves_safe_next(self) -> None:
        """メール重複時のログインリンクが安全なnextを保持すること."""
        next_url = '/event/speaker-link/signed-token/'

        response = render_duplicate_email_signup(next_url)

        self.assertTrue(has_next_link(
            collect_hrefs(response),
            reverse('account_login'),
            next_url,
        ))

    def test_duplicate_email_login_link_ignores_raw_external_next(self) -> None:
        """allauthログインが外部nextへリダイレクトしないこと."""
        external_url = 'https://evil.example.com/path'
        login_url = reverse('account_login')
        make_user(
            user_name='external_next_login_user',
            email='external-next-login@example.com',
        )

        get_response = self.client.get(login_url, {'next': external_url})
        post_response = self.client.post(login_url, {
            'login': 'external_next_login_user',
            'password': 'testpass123',
            'next': external_url,
        })

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(post_response.url, reverse('event:my_presentations'))
        self.assertNotEqual(post_response.url, external_url)

    def test_allauth_login_without_membership_uses_my_presentations(self) -> None:
        """allauthのログイン後も集会未所属ユーザーは自分の発表へ遷移する。"""
        user = make_user(
            user_name='allauth_no_membership_user',
            email='allauth-no-membership@example.com',
        )

        response = self.client.post(reverse('account_login'), {
            'login': user.user_name,
            'password': 'testpass123',
        })

        self.assertRedirects(
            response,
            reverse('event:my_presentations'),
            fetch_redirect_response=False,
        )

    def test_allauth_login_with_membership_uses_existing_default(self) -> None:
        """allauthのログイン後も集会所属ユーザーは既存の既定先へ遷移する。"""
        user = make_user(
            user_name='allauth_membership_user',
            email='allauth-membership@example.com',
        )
        make_community(name='allauth遷移テスト集会', owner=user)

        response = self.client.post(reverse('account_login'), {
            'login': user.user_name,
            'password': 'testpass123',
        })

        self.assertRedirects(
            response,
            settings.LOGIN_REDIRECT_URL,
            fetch_redirect_response=False,
        )

    def test_allauth_login_prefers_safe_next(self) -> None:
        """allauthログインでも安全なnextが既定先より優先される。"""
        user = make_user(
            user_name='allauth_next_user',
            email='allauth-next@example.com',
        )
        next_url = reverse('account:settings')

        response = self.client.post(
            f"{reverse('account_login')}?next={next_url}",
            {
                'login': user.user_name,
                'password': 'testpass123',
            },
        )

        self.assertRedirects(response, next_url, fetch_redirect_response=False)


@tag('offline_external_api')
class SocialSignupRedirectTests(TestCase):
    """Discord OAuthの新規登録完了後のリダイレクトを検証する。"""

    def setUp(self) -> None:
        self.factory = RequestFactory()

    def _complete_social_signup(self, next_url: str = ''):
        request = self.factory.get('/accounts/discord/login/', {'next': next_url})
        request.user = AnonymousUser()
        SessionMiddleware(lambda request: None).process_request(request)
        request.session.save()
        MessageMiddleware(lambda request: None).process_request(request)

        user = User(
            user_name='new_discord_signup_user',
            display_name='New Discord Signup User',
            email='new-discord-signup@example.com',
        )
        sociallogin = SocialLogin(
            user=user,
            account=SocialAccount(
                provider='discord',
                uid='new-discord-signup-id',
                extra_data={
                    'email': user.email,
                    'verified': True,
                },
            ),
            email_addresses=[EmailAddress(
                email=user.email,
                verified=True,
                primary=True,
            )],
        )
        with request_context(request):
            sociallogin.state = SocialLogin.state_from_request(request)
            return process_signup(request, sociallogin)

    def test_new_social_signup_without_membership_uses_my_presentations(self) -> None:
        """新規Discord登録の集会未所属ユーザーは自分の発表へ遷移する。"""
        response = self._complete_social_signup()

        self.assertRedirects(
            response,
            reverse('event:my_presentations'),
            fetch_redirect_response=False,
        )

    def test_new_social_signup_prefers_safe_next(self) -> None:
        """新規Discord登録でも安全なnextが既定遷移先より優先される。"""
        next_url = reverse('account:settings')

        response = self._complete_social_signup(next_url)

        self.assertRedirects(response, next_url, fetch_redirect_response=False)
