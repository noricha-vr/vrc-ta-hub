"""docs/deploy-check.toml の expect_body が、今のコードのページに含まれるかを確かめるテスト。

ページの文言を変えた PR で expect_body の直し忘れがあると、その後のデプロイが
カナリアの途中でロールバックする（2026-10 に 2 回発生、#893）。Django のテストクライアントで
GET できる確認だけを対象に、PR の CI で先に気づけるようにする。
"""

import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from django.test import TestCase


REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_CHECK_PATH = REPO_ROOT / 'docs' / 'deploy-check.toml'
PRODUCTION_HOST = 'vrc-ta-hub.com'


def _load_checks():
    config = tomllib.loads(DEPLOY_CHECK_PATH.read_text(encoding='utf-8'))
    checks = []
    for level, items in config.get('checks', {}).items():
        for item in items:
            checks.append({**item, 'level': level})
    return checks


def find_expectation_failures(client, checks):
    """GET できる確認のうち、ステータスか expect_body が合わないものを文字列で返す。"""
    failures = []
    for check in checks:
        if check.get('method', 'GET').upper() != 'GET':
            continue
        parts = urlsplit(check['url'])
        if parts.hostname != PRODUCTION_HOST:
            continue
        path = parts.path + (f'?{parts.query}' if parts.query else '')
        response = client.get(path, HTTP_HOST=PRODUCTION_HOST, secure=True)
        label = f"{check['id']} {check['name']} ({path})"
        expected_status = check.get('expect_status')
        if expected_status is not None and response.status_code != expected_status:
            failures.append(f'{label}: status {response.status_code} != {expected_status}')
            continue
        expect_body = check.get('expect_body')
        if expect_body is not None and expect_body not in response.content.decode('utf-8'):
            failures.append(f'{label}: 本文に {expect_body!r} が無い')
    return failures


class DeployCheckExpectBodyTest(TestCase):
    def test_every_expect_body_is_present_in_current_pages(self):
        """今のコードで、toml の各 expect_body がページ本文に含まれる。"""
        checks = _load_checks()
        self.assertGreaterEqual(len(checks), 8)
        failures = find_expectation_failures(self.client, checks)
        self.assertEqual(
            failures, [],
            'docs/deploy-check.toml の expect_body が現在のページと合っていません。'
            'ページの文言を変えたなら toml も直してください（直さないとデプロイがロールバックします）:\n'
            + '\n'.join(failures),
        )

    def test_detects_expect_body_missing_from_page(self):
        """本文に無い文言へ差し替えた設定は失敗として検出される。"""
        checks = [{
            'id': 'X1', 'name': 'トップ', 'level': 'critical', 'method': 'GET',
            'url': f'https://{PRODUCTION_HOST}/',
            'expect_status': 200, 'expect_body': '存在しないはずの文言-9f3a1c',
        }]
        failures = find_expectation_failures(self.client, checks)
        self.assertEqual(len(failures), 1)
        self.assertIn('X1', failures[0])
