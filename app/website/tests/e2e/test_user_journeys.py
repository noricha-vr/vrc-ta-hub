"""主要ユーザー導線を実ブラウザで検証するE2Eテスト."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest import skipUnless
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
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


class PlaywrightLiveServerTestCase(StaticLiveServerTestCase):
    """Chromiumを起動し、テストごとに独立したブラウザ状態を提供する."""

    playwright: Any = None
    browser: Any = None
    context: Any = None
    page: Any = None
    django_allow_async_unsafe: str | None = None

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
        """独立contextを作り、外部HTTP通信とキャッシュ汚染を遮断する."""
        super().setUp()
        cache.clear()
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

    def tearDown(self) -> None:
        """最終画面とtraceを保存し、ブラウザ状態を破棄する."""
        try:
            if self.page is not None and not self.page.is_closed():
                self.page.screenshot(path=str(self.screenshot_path), full_page=True)
        except Exception as exc:
            logger.warning('E2E screenshot capture failed: %s', exc)

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

    def _route_request(self, route: Any) -> None:
        """ライブサーバー以外へのブラウザHTTP通信を中断する."""
        parsed = urlparse(route.request.url)
        if parsed.scheme in {'http', 'https'} and parsed.hostname not in {'localhost', '127.0.0.1'}:
            route.abort()
            return
        route.continue_()

    def login(self, user_name: str, password: str) -> None:
        """公開ログインフォームからユーザーを認証する."""
        self.page.goto(f'{self.live_server_url}{reverse("account:login")}')
        self.page.get_by_label('ユーザー名').fill(user_name)
        self.page.get_by_label('パスワード').fill(password)
        self.page.get_by_role('button', name='ログイン', exact=True).click()
        self.page.wait_for_load_state('domcontentloaded')

    def logout(self) -> None:
        """アカウント設定にあるPOSTフォームからログアウトする."""
        self.page.goto(f'{self.live_server_url}{reverse("account:settings")}')
        self.page.get_by_role('main').get_by_role(
            'button', name='ログアウト', exact=True
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

    password = 'e2e-test-password'
    community_name = 'E2E 技術集会'
    published_theme = '公開済みE2E発表'

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
        self.community = make_community(
            name=self.community_name,
            owner=self.owner,
            accepts_lt_application=True,
            description='E2Eテスト用の公開集会です。',
            poster_image='poster/e2e-community.png',
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
        self.page.get_by_role('link', name='発表を申し込む', exact=True).click()
        self.page.get_by_label('開催日').select_option(str(self.event.pk))
        self.page.get_by_label('テーマ').fill(application_theme)
        self.page.get_by_label('発表者名').fill('E2E Applicant')
        self.page.get_by_role('button', name='申請する', exact=True).click()
        expect(self.page.get_by_role('heading', name='発表申請完了', exact=True)).to_be_visible()

        application = EventDetail.objects.get(event=self.event, theme=application_theme)
        self.logout()
        self.login(self.owner.user_name, self.password)
        self.page.goto(
            f'{self.live_server_url}{reverse("event:lt_application_review", kwargs={"pk": application.pk})}'
        )
        self.page.get_by_label('承認する').check()
        self.page.get_by_role('button', name='決定', exact=True).click()
        expect(self.page.get_by_text('申請を承認しました。', exact=True)).to_be_visible()

        application.refresh_from_db()
        self.assertEqual(application.status, 'approved')
        self.logout()
        self.page.goto(
            f'{self.live_server_url}{reverse("event:detail", kwargs={"pk": application.pk})}'
        )
        expect(self.page.get_by_role('heading', name=application_theme, exact=True)).to_be_visible()
