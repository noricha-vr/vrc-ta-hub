"""一時設定・DB・開発サーバーの寿命を撮影の一回に限定する。"""
import errno
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = '''from website.settings import *  # noqa: F401,F403
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
# 集計期間・受付・自動更新日時も共通デモの基準日に固定する。一時設定内だけで有効。
_guide_clock = patch('django.utils.timezone.now', return_value=datetime(2030, 4, 1, 3, tzinfo=timezone.utc))
_guide_clock.start()
GUIDE_CAPTURE_EXTENDED_DEMO = True
ROOT_URLCONF = 'guide_demo_urls'
GUIDE_DEMO_ROOT = Path(__file__).resolve().parent
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(GUIDE_DEMO_ROOT / 'demo.sqlite3')}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
MEDIA_ROOT = GUIDE_DEMO_ROOT / 'media'
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
ACCOUNT_DEFAULT_HTTP_PROTOCOL = 'http'
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
TURNSTILE_SITE_KEY = ''
TURNSTILE_SECRET_KEY = ''
DEBUG_LOGIN_SKIP = False
'''


def free_port():
    preferred = int(os.environ.get('GUIDE_PORT', '8019'))
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', preferred))
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
            sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def stop_server(server):
    if server.poll() is None:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


def main():
    locked = next(line.split('==', 1)[1] for line in (ROOT / 'requirements-e2e.txt').read_text().splitlines()
                  if line.startswith('playwright=='))
    if version('playwright') != locked:
        raise RuntimeError(f'Playwright は requirements-e2e.txt と同じ {locked} を使ってください')
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    with tempfile.TemporaryDirectory(prefix='guide-demo-') as tmp:
        (Path(tmp) / 'guide_demo_settings.py').write_text(SETTINGS)
        (Path(tmp) / 'guide_demo_urls.py').write_text(
            'from website.urls import urlpatterns\n'
            'from django.conf import settings\n'
            'from django.conf.urls.static import static\n'
            'from django.http import HttpResponse\n'
            'from django.urls import path\n'
            'def ready(request):\n'
            '    return HttpResponse(settings.GUIDE_DEMO_ROOT.name)\n'
            'urlpatterns = [path("__guide_capture_ready__/", ready), *urlpatterns,\n'
            '    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)]\n'
        )
        # デモユーザーのパスワードは 1 回の撮影でだけ使う使い捨て（DB も撮影後に消える）。
        env = dict(os.environ, DJANGO_SETTINGS_MODULE='guide_demo_settings', FERNET_KEY=Fernet.generate_key().decode(),
                   GUIDE_DEMO_PASSWORD=secrets.token_urlsafe(16),
                   PYTHONPATH=os.pathsep.join([tmp, str(ROOT / 'app')]))
        command = [sys.executable, '-m', 'tests.offline_manage']
        for args in (['migrate', '--noinput'], ['seed_guide_demo']):
            subprocess.run([*command, *args], cwd=ROOT / 'app', env=env, check=True, stdout=subprocess.DEVNULL)
        # migration 中に別の撮影が起動することがあるため、待受ポートは起動直前に選ぶ。
        port = free_port()
        base_url = f'http://127.0.0.1:{port}'
        with (Path(tmp) / 'server.log').open('w+') as log:
            server = subprocess.Popen([*command, 'runserver', f'127.0.0.1:{port}', '--noreload'],
                                      cwd=ROOT / 'app', env=env, stdout=log, stderr=log)
            try:
                for _ in range(120):
                    if server.poll() is not None:
                        raise RuntimeError('撮影用サーバーが起動前に終了しました')
                    try:
                        with urlopen(f'{base_url}/__guide_capture_ready__/', timeout=1) as response:
                            if response.status == 200 and response.read().decode() == Path(tmp).name:
                                break
                    except (URLError, TimeoutError):
                        time.sleep(0.25)
                else:
                    raise RuntimeError('撮影用サーバーの起動がタイムアウトしました')
                subprocess.run([sys.executable, str(Path(__file__).with_name('capture.py')), base_url, *sys.argv[1:]],
                               cwd=ROOT, env=env, check=True)
            except BaseException:
                # URL や入力値を含み得るアクセスログは標準出力に転記しない。
                print(json.dumps({'event': 'guide_capture_failed', 'server_exit': server.poll()}), file=sys.stderr)
                raise
            finally:
                stop_server(server)


if __name__ == '__main__':
    main()
