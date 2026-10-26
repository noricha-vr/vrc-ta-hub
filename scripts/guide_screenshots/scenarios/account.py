"""一時 SQLite 上の実 view を描画して撮る。Discord とメール本文は説明用モック。

メール確認・初回設定などの状態は Django Client で作り、ローカルの同じ
URL へのブラウザ要求にそのレスポンスを返す。外部メール/OAuth は送らない。
各シナリオの DB 変更はロールバックし、PC/スマホで同じ初期状態を使う。
"""
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from capture import scenario


@contextmanager
def account_context(capture):
    import django
    django.setup()
    from django.conf import settings
    from django.db import connection, transaction
    from django.test import Client
    # サーバー側のアクセス計測と競合しないよう、撮影専用 DB をさらにコピーする。
    # 通常 DB へ接続しないことを、共通 seed と同じ条件で確認する。
    source = Path(settings.DATABASES['default']['NAME'])
    root = Path(settings.GUIDE_DEMO_ROOT)
    if connection.vendor != 'sqlite' or source.parent != root:
        raise RuntimeError('アカウント撮影は一時 SQLite 環境でのみ実行できます')
    with tempfile.TemporaryDirectory(dir=root, prefix='account-') as tmp:
        database = Path(tmp) / 'account.sqlite3'
        shutil.copy2(source, database)
        # Playwright 同期 API のループ内でも、専用 DB への操作は直列で実行する。
        with patch.dict(os.environ, DJANGO_ALLOW_ASYNC_UNSAFE='true'):
            connection.close()
            with patch.dict(connection.settings_dict, NAME=str(database)):
                try:
                    with transaction.atomic():
                        client = Client(HTTP_HOST='127.0.0.1')
                        yield AccountScreens(capture, client)
                        transaction.set_rollback(True)
                finally:
                    connection.close()


class AccountScreens:
    def __init__(self, capture, client):
        self.capture = capture
        self.client = client

    def user(self, role='general'):
        from guide.demo import DEMO_USERS
        from user_account.models import CustomUser
        return CustomUser.objects.get(email=DEMO_USERS[role][1])

    def login(self, role='general'):
        self.client.force_login(self.user(role))

    def show(self, path, *, response=None, **settings):
        from django.test import override_settings
        with override_settings(**settings):
            response = response or self.client.get(path, follow=True)
        if response.status_code != 200:
            raise RuntimeError(f'アカウント画面の描画失敗: {path}')
        # ブラウザは実 view の HTML を表示。設定の分岐を撮影環境だけで切り替える。
        url = self.capture.base_url + path
        self.capture.page.route(url, lambda route: route.fulfill(
            status=200, content_type='text/html; charset=utf-8', body=response.content))
        self.capture.annotations.clear()
        self.capture.masks.clear()
        self.capture.page.goto(url, wait_until='networkidle')
        self.capture.page.evaluate('document.fonts.ready')
        self.capture.page.unroute(url)

    def shot(self, *selectors, area='.card'):
        # フォーカスやマウス位置で入力欄の描画が揺れない撮影状態にそろえる。
        self.capture.page.evaluate('document.activeElement?.blur()')
        self.capture.page.mouse.move(0, 0)
        self.capture.page.wait_for_timeout(200)
        for number, selector in enumerate(selectors, 1):
            self.capture.annotate(selector, number)
        if area == 'body':
            # allauth 標準画面は本文が全幅。文字の実寸を測り、メニューと余白を除く。
            self.capture.page.locator('h1').evaluate("e => e.style.width = 'fit-content'")
            clip = self.capture.page.evaluate("""() => {
                const heading = document.querySelector('h1');
                const top = heading.getBoundingClientRect().top + scrollY - 20;
                let right = 0, bottom = 0;
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                while (walker.nextNode()) {
                    const node = walker.currentNode;
                    if (!node.textContent.trim() || node.parentElement.closest('script,style')) continue;
                    const range = document.createRange(); range.selectNodeContents(node);
                    for (const rect of range.getClientRects()) {
                        if (rect.top + scrollY < top) continue;
                        right = Math.max(right, rect.right);
                        bottom = Math.max(bottom, rect.bottom + scrollY);
                    }
                }
                for (const input of document.querySelectorAll('input:not([type=hidden])')) {
                    const r = input.getBoundingClientRect(); right = Math.max(right, r.right);
                }
                return {x:0, y:Math.max(0, top), width:Math.min(innerWidth, right + 32),
                        height:bottom + 22 - Math.max(0, top)};
            }""")
            self.capture.shot(clip=clip)
        elif area.startswith(('a[', 'main form[', 'main a[', '.mb-3:')):
            # 一行メニューは上の行の文字が混ざらない範囲まで切り詰める。
            target = self.capture.page.locator(area)
            target.scroll_into_view_if_needed()
            box = target.bounding_box()
            scroll = self.capture.page.evaluate('({x:scrollX, y:scrollY})')
            left = max(0, box['x'] - 16)
            padding = 0 if area.startswith('.mb-3:') else 18
            self.capture.shot(clip={'x': left, 'y': max(0, box['y'] + scroll['y'] - padding),
                                    'width': min(box['width'] + 32, self.capture.page.viewport_size['width'] - left),
                                    'height': box['height'] + padding + 6})
        else:
            self.capture.shot(selector=area)
        self.capture.annotations.clear()

    def verify_guide(self):
        self.capture.verify_guide()

    def confirmation(self, email):
        from allauth.account.models import EmailAddress, EmailConfirmationHMAC
        address, _ = EmailAddress.objects.get_or_create(user=self.user(), email=email,
                                                       defaults={'primary': False, 'verified': False})
        if address.verified:
            address.verified = False
            address.save(update_fields=['verified'])
        return '/accounts/confirm-email/' + EmailConfirmationHMAC(address).key + '/'


@scenario('account/login', mobile=True)
def login(capture):
    with account_context(capture) as s:
        s.show('/account/register/')
        s.shot('a[href*="/accounts/discord/login/"]')  # 01 Discord登録
        capture.mock('discord-oauth.html')
        s.shot('#authorize', area='.oauth')  # 02 Discord認証
        s.show('/account/register/', SOCIALACCOUNT_PROVIDERS={})
        capture.page.locator('#id_user_name').fill('ヒナ')
        capture.page.locator('#id_email').fill('hina@example.com')
        s.shot('#id_user_name', '#id_email', '.card button[type=submit]')  # 03 メール登録
        # OAuth 戻り先の状態だけを作り、追加登録の実 view を開く。
        from allauth.socialaccount.adapter import get_adapter
        from allauth.socialaccount.models import SocialAccount, SocialLogin
        from user_account.models import CustomUser
        sociallogin = SocialLogin(
            provider=get_adapter().get_provider(None, 'discord'),
            user=CustomUser(user_name='ヒナ', email='hina@example.com'),
            account=SocialAccount(provider='discord', uid='guide-demo-signup'))
        session = s.client.session
        session['socialaccount_sociallogin'] = sociallogin.serialize()
        session.save()
        s.show('/accounts/3rdparty/signup/')
        s.shot('input[name=email]', 'input[name=user_name]', '.card button[type=submit]')  # 04
        capture.mock('discord-oauth-email.html')
        s.shot('#confirm-email', area='.mail')  # 05 確認メール
        s.show(s.confirmation('hina@example.com'))
        s.shot('button[type=submit]', area='body')  # 06 メール確認
        s.show('/account/login/', TURNSTILE_SITE_KEY='guide-demo', TURNSTILE_SECRET_KEY='DUMMY')
        capture.page.locator('#id_username').fill('hina@example.com')
        s.shot('#id_username', '#id_password', '#turnstile-help', '#login-submit', area='form:has(#id_username)')  # 07 ログイン
        s.shot('a[href*="/accounts/discord/login/"]', area='.card .text-center.my-5')  # 08 Discordログイン
        s.login()
        s.show('/account/settings/')
        s.shot('main form[action="/account/logout/"] button', area='main form[action="/account/logout/"]')  # 09 ログアウト
        s.verify_guide()


@scenario('account/discord', mobile=True)
def discord(capture):
    with account_context(capture) as s:
        s.login()
        s.show('/account/discord-required/')
        s.shot('.hub-admin a[href*="/accounts/discord/login/"]')  # 01 案内
        s.show('/account/settings/')
        s.shot('a[href="/accounts/3rdparty/"]', area='a[href="/accounts/3rdparty/"]')  # 02 設定
        capture.mock('discord-oauth.html')
        s.shot('#authorize', area='.oauth')  # 03 認証
        s.show('/accounts/3rdparty/')
        s.shot('a[href*="/disconnect/"]')  # 04 解除入口
        account = s.user().socialaccount_set.get(provider='discord')
        s.show(f'/account/social/{account.pk}/disconnect/')
        s.shot('#id_password', '.confirm-panel button[type=submit]')  # 05 解除確認
        # 未連携の場合は「連携を管理」でなく連携開始リンクになる。
        account.delete()
        s.show('/account/settings/')
        s.shot('main a[href*="/accounts/discord/login/"]', area='main a[href*="/accounts/discord/login/"]')  # 06 未連携
        s.verify_guide()


@scenario('account/password', mobile=True)
def password(capture):
    with account_context(capture) as s:
        from allauth.account.forms import default_token_generator
        from allauth.account.utils import user_pk_to_url_str
        from guide.demo import demo_password
        s.login()
        s.show('/account/settings/')
        s.shot('a[href="/account/password_change/"]', area='a[href="/account/password_change/"]')  # 01
        s.show('/account/password_change/')
        s.shot('#id_old_password', '#id_new_password1', '#account-edit-form button[type=submit]')  # 02
        user = s.user()
        user.set_unusable_password()
        user.save(update_fields=['password'])
        s.login()
        s.show('/accounts/password/set/')
        s.shot('#id_password1', '#id_password2', 'button[name=action]', area='body')  # 03
        user.set_password(demo_password())
        user.save(update_fields=['password'])
        s.client.logout()
        s.show('/account/login/')
        s.shot('a[href="/accounts/password/reset/"]', area='.mb-3:has(#id_password)')  # 04
        s.show('/accounts/password/reset/')
        s.shot('#id_email', 'form button[type=submit]', area='body')  # 05
        s.show('/accounts/password/reset/done/')
        s.shot('h1', area='body')  # 06
        user.refresh_from_db()
        token = default_token_generator.make_token(user)
        path = f'/accounts/password/reset/key/{user_pk_to_url_str(user)}-{token}/'
        response = s.client.get(path, follow=True)
        actual_path = urlsplit(response.redirect_chain[-1][0]).path if response.redirect_chain else path
        s.show(actual_path, response=response)
        s.shot('#id_password1', '#id_password2', 'button[name=action]', area='body')  # 07
        response = s.client.post(actual_path, {'password1': demo_password(), 'password2': demo_password()}, follow=True)
        if response.wsgi_request.path != '/accounts/password/reset/key/done/':
            raise RuntimeError('パスワード再設定の完了を確認できませんでした')
        s.show('/accounts/password/reset/key/done/', response=response)
        s.shot('h1', area='body')  # 08
        s.verify_guide()


@scenario('account/profile', mobile=True)
def profile(capture):
    with account_context(capture) as s:
        s.login()
        s.show('/account/settings/')
        s.shot('a[href="/account/update/"]', area='a[href="/account/update/"]')  # 01
        s.show('/account/update/')
        # 実在のユーザー名を例示する既存 placeholder を撮影画面だけ伏せる。
        capture.page.locator('#id_x_account').evaluate("e => e.placeholder = 'hina_demo'")
        s.shot('#id_display_name', '#id_user_name')  # 02
        capture.page.locator('#id_email').fill('hina-new@example.com')
        s.shot('#id_email', 'button[form=profile-form]')  # 03
        s.show(s.confirmation('hina-new@example.com'))
        s.shot('button[type=submit]', area='body')  # 04
        s.verify_guide()
