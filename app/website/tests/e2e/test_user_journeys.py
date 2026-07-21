"""主要ユーザー導線を実ブラウザで検証するE2Eテスト."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest import skipUnless
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings, tag
from django.urls import reverse

from event.models import EventDetail
from event.tests.tweet_generation import TweetGenerationPatchMixin
from tests.factories import (
    make_community,
    make_discord_linked_user,
    make_event,
    make_event_detail,
)

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    expect = None
    sync_playwright = None


logger = logging.getLogger(__name__)
PLAYWRIGHT_AVAILABLE = sync_playwright is not None
EXTERNAL_UI_ASSET_PREFIXES = (
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/',
    'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/',
)
EXTERNAL_UI_RESOURCE_TYPES = frozenset({'stylesheet', 'script', 'font', 'image'})


class PlaywrightLiveServerTestCase(StaticLiveServerTestCase):
    """Chromiumを起動し、テストごとに独立したブラウザ状態を提供する."""

    playwright: Any = None
    browser: Any = None
    context: Any = None
    page: Any = None
    django_allow_async_unsafe: str | None = None
    external_ui_asset_failures: list[str]

    @classmethod
    def setUpClass(cls) -> None:
        """ライブサーバーと共有Chromiumプロセスを起動する."""
        super().setUpClass()
        # Playwright同期APIは内部でasyncioを使うため、同じスレッドの同期ORMを
        # Djangoが拒否する。テストプロセス内のPlaywright稼働中だけ許可する。
        cls.django_allow_async_unsafe = os.environ.get('DJANGO_ALLOW_ASYNC_UNSAFE')
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
        try:
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception:
            cls._restore_django_async_guard()
            super().tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        """Chromiumとライブサーバーを確実に停止する."""
        try:
            if cls.browser is not None:
                cls.browser.close()
            if cls.playwright is not None:
                cls.playwright.stop()
        finally:
            cls._restore_django_async_guard()
            super().tearDownClass()

    @classmethod
    def _restore_django_async_guard(cls) -> None:
        """Playwright起動前のDjango非同期安全ガードへ戻す."""
        if cls.django_allow_async_unsafe is None:
            os.environ.pop('DJANGO_ALLOW_ASYNC_UNSAFE', None)
        else:
            os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = cls.django_allow_async_unsafe

    def setUp(self) -> None:
        """独立contextを作り、外部通信を制限してキャッシュ汚染を遮断する."""
        super().setUp()
        cache.clear()
        self.external_ui_asset_failures = []
        artifact_root = Path(settings.BASE_DIR).parent / 'test-results' / 'e2e'
        artifact_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        artifact_stem = f'{self.__class__.__name__}-{self._testMethodName}-{timestamp}'
        self.screenshot_path = artifact_root / f'{artifact_stem}.png'
        self.trace_path = artifact_root / f'{artifact_stem}-trace.zip'

        self.context = self.browser.new_context(viewport={'width': 1280, 'height': 900})
        self.context.route('**/*', self._route_request)
        self.context.tracing.start(screenshots=True, snapshots=True, sources=True)
        self.page = self.context.new_page()
        self.page.on('requestfailed', self._record_external_ui_asset_request_failure)
        self.page.on('response', self._record_external_ui_asset_response_failure)

    def tearDown(self) -> None:
        """最終画面とtraceを保存し、ブラウザ状態を破棄する."""
        ui_asset_error: Exception | None = None
        try:
            self._assert_ui_assets_loaded()
        except Exception as exc:
            ui_asset_error = exc

        try:
            if self.page is not None and not self.page.is_closed():
                self.page.screenshot(path=str(self.screenshot_path), full_page=True)
        except Exception as exc:
            logger.warning('E2E screenshot capture failed: %s', exc)

        try:
            self._assert_external_ui_assets_succeeded()
        except Exception as exc:
            ui_asset_error = ui_asset_error or exc

        try:
            if self.context is not None:
                self.context.tracing.stop(path=str(self.trace_path))
        except Exception as exc:
            logger.warning('E2E trace capture failed: %s', exc)
        finally:
            try:
                if self.context is not None:
                    self.context.close()
            finally:
                self.context = None
                self.page = None
                super().tearDown()

        if ui_asset_error is not None:
            raise ui_asset_error

    def _route_request(self, route: Any) -> None:
        """表示用静的資産を除きライブサーバー外へのHTTP通信を中断する."""
        parsed = urlparse(route.request.url)
        is_external_http = (
            parsed.scheme in {'http', 'https'}
            and parsed.hostname not in {'localhost', '127.0.0.1'}
        )
        if is_external_http and not self._is_allowed_external_ui_asset(route.request):
            route.abort()
            return
        route.continue_()

    @staticmethod
    def _is_allowed_external_ui_asset(request: Any) -> bool:
        """許可リスト内の読み取り専用UI資産かを返す."""
        return (
            request.method == 'GET'
            and request.resource_type in EXTERNAL_UI_RESOURCE_TYPES
            and request.url.startswith(EXTERNAL_UI_ASSET_PREFIXES)
        )

    def _record_external_ui_asset_request_failure(self, request: Any) -> None:
        """許可したUI資産の通信失敗を記録する."""
        if self._is_allowed_external_ui_asset(request):
            reason = request.failure or 'request failed'
            # 次画面への遷移で不要になった画像・フォントはブラウザが正常に中断する。
            if reason != 'net::ERR_ABORTED':
                self.external_ui_asset_failures.append(f'{request.url}: {reason}')

    def _record_external_ui_asset_response_failure(self, response: Any) -> None:
        """許可したUI資産のHTTPエラーを記録する."""
        if response.status >= 400 and self._is_allowed_external_ui_asset(response.request):
            self.external_ui_asset_failures.append(f'{response.url}: HTTP {response.status}')

    def _assert_ui_assets_loaded(self) -> None:
        """外部UI資産とBootstrapの適用完了を検証する."""
        if self.page is None or self.page.is_closed() or not self.page.url.startswith(
            self.live_server_url
        ):
            return

        self.page.wait_for_function("document.fonts.status === 'loaded'")
        expect(self.page.locator('.navbar-nav')).to_have_css('display', 'flex')
        self.page.wait_for_function("typeof window.bootstrap !== 'undefined'")
        self._assert_external_ui_assets_succeeded()

    def _assert_external_ui_assets_succeeded(self) -> None:
        """許可した外部UI資産に通信失敗がないことを検証する."""
        self.assertFalse(
            self.external_ui_asset_failures,
            'UI asset loading failed:\n' + '\n'.join(self.external_ui_asset_failures),
        )

    def login(self, user_name: str, password: str) -> None:
        """公開ログインフォームからユーザーを認証する."""
        self.page.goto(f'{self.live_server_url}{reverse("account:login")}')
        self.page.get_by_label('ユーザー名').fill(user_name)
        self.page.get_by_label('パスワード').fill(password)
        self.page.get_by_role('button', name=re.compile(r'ログイン$')).click()
        self.page.wait_for_load_state('domcontentloaded')

    def logout(self) -> None:
        """アカウント設定にあるPOSTフォームからログアウトする."""
        self.page.goto(f'{self.live_server_url}{reverse("account:settings")}')
        self.page.get_by_role('main').get_by_role(
            'button', name=re.compile(r'ログアウト$')
        ).click()
        self.page.wait_for_load_state('domcontentloaded')


@tag('e2e')
@skipUnless(PLAYWRIGHT_AVAILABLE, 'Playwright E2E dependencies are not installed')
@override_settings(
    DEBUG_LOGIN_SKIP=False,
    DISCORD_AUTH_REQUIRED=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class UserJourneysE2ETests(TweetGenerationPatchMixin, PlaywrightLiveServerTestCase):
    """公開閲覧・認証・発表申請の主要3導線を検証する."""

    media_root: tempfile.TemporaryDirectory[str] | None = None
    media_settings_override: Any = None
    password = 'e2e-test-password'
    community_name = 'E2E 技術集会'
    published_theme = '公開済みE2E発表'

    @classmethod
    def setUpClass(cls) -> None:
        """テストメディアをローカル一時ストレージへ隔離する."""
        cls.media_root = tempfile.TemporaryDirectory(prefix='vrc-ta-hub-e2e-media-')
        cls.media_settings_override = override_settings(
            MEDIA_ROOT=cls.media_root.name,
            STORAGES={
                'default': {
                    'BACKEND': 'django.core.files.storage.FileSystemStorage',
                },
                'staticfiles': settings.STORAGES['staticfiles'],
            },
        )
        cls.media_settings_override.enable()
        try:
            super().setUpClass()
        except Exception:
            cls._restore_media_settings()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        """一時メディアとストレージ設定を確実に破棄する."""
        try:
            super().tearDownClass()
        finally:
            cls._restore_media_settings()

    @classmethod
    def _restore_media_settings(cls) -> None:
        """E2E開始前のメディアストレージ設定へ戻す."""
        if cls.media_settings_override is not None:
            cls.media_settings_override.disable()
            cls.media_settings_override = None
        if cls.media_root is not None:
            cls.media_root.cleanup()
            cls.media_root = None

    def setUp(self) -> None:
        """各シナリオ用のユーザー・集会・開催日・公開発表を作成する."""
        super().setUp()
        self.owner = make_discord_linked_user(
            user_name='e2e_owner',
            email='e2e-owner@example.test',
            password=self.password,
            display_name='E2E Owner',
        )
        self.applicant = make_discord_linked_user(
            user_name='e2e_applicant',
            email='e2e-applicant@example.test',
            password=self.password,
            display_name='E2E Applicant',
        )
        poster_path = (
            Path(settings.BASE_DIR)
            / 'guide/static/guide/images/posters/vrc-ta-hub-poster-1.jpg'
        )
        poster_image = SimpleUploadedFile(
            'e2e-community-poster.jpg',
            poster_path.read_bytes(),
            content_type='image/jpeg',
        )
        self.community = make_community(
            name=self.community_name,
            owner=self.owner,
            accepts_lt_application=True,
            description='E2Eテスト用の公開集会です。',
            poster_image=poster_image,
        )
        self.event = make_event(self.community, accepts_lt_application=True)
        self.published_detail = make_event_detail(
            self.event,
            applicant=self.owner,
            status='approved',
            speaker='公開発表者',
            theme=self.published_theme,
        )

    def test_public_browsing_from_home_to_presentation(self) -> None:
        """トップから集会と発表の公開詳細までリンクで移動できる."""
        self.page.goto(self.live_server_url)
        self.page.get_by_role('link', name='集会一覧', exact=True).first.click()
        expect(self.page.get_by_role('heading', name='VRChat 技術・学術系集会一覧')).to_be_visible()

        self.page.get_by_role('link').filter(has_text=self.community_name).first.click()
        expect(self.page.get_by_role('heading', name=self.community_name, exact=True)).to_be_visible()

        self.page.get_by_role('link', name=self.published_theme, exact=True).first.click()
        expect(self.page).to_have_url(
            re.compile(rf'{re.escape(reverse("event:detail", kwargs={"pk": self.published_detail.pk}))}$')
        )
        expect(self.page.get_by_role('heading', name=self.published_theme, exact=True)).to_be_visible()

    def test_login_settings_logout_and_protected_redirect(self) -> None:
        """ログイン状態が設定画面へ反映され、ログアウト後は保護される."""
        self.login(self.applicant.user_name, self.password)
        self.page.goto(f'{self.live_server_url}{reverse("account:settings")}')
        expect(self.page.get_by_role('heading', name='アカウント設定', exact=True)).to_be_visible()

        self.logout()
        expect(self.page.get_by_role('heading', name='ログイン', exact=True)).to_be_visible()

        self.page.goto(f'{self.live_server_url}{reverse("account:settings")}')
        expect(self.page).to_have_url(re.compile(r'/account/login/\?next=/account/settings/$'))

    def test_application_approval_becomes_public_presentation(self) -> None:
        """発表者の申請が主催者承認後に匿名公開される."""
        application_theme = '申請から公開されるE2E発表'
        self.login(self.applicant.user_name, self.password)
        self.page.goto(
            f'{self.live_server_url}{reverse("community:detail", kwargs={"pk": self.community.pk})}'
        )
        self.page.get_by_role('link', name=re.compile(r'発表を申し込む$')).click()
        self.page.get_by_label('開催日').select_option(str(self.event.pk))
        self.page.get_by_label('テーマ').fill(application_theme)
        self.page.get_by_label('発表者名').fill('E2E Applicant')
        self.page.get_by_role('button', name=re.compile(r'申請する$')).click()
        expect(self.page.get_by_role('heading', name='発表申請完了', exact=True)).to_be_visible()

        application = EventDetail.objects.get(event=self.event, theme=application_theme)
        self.logout()
        self.login(self.owner.user_name, self.password)
        self.page.goto(
            f'{self.live_server_url}{reverse("event:lt_application_review", kwargs={"pk": application.pk})}'
        )
        self.page.get_by_label('承認する').check()
        self.page.get_by_role('button', name=re.compile(r'決定$')).click()
        expect(self.page.get_by_text('申請を承認しました。', exact=True)).to_be_visible()

        application.refresh_from_db()
        self.assertEqual(application.status, 'approved')
        self.logout()
        self.page.goto(
            f'{self.live_server_url}{reverse("event:detail", kwargs={"pk": application.pk})}'
        )
        expect(self.page.get_by_role('heading', name=application_theme, exact=True)).to_be_visible()
