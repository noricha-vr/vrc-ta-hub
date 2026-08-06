"""docs/deploy-check.toml（deploy-watch が読むデプロイ前チェック定義）のテスト。"""

import tomllib
from pathlib import Path

from django.test import SimpleTestCase


REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_CHECK_PATH = REPO_ROOT / 'docs' / 'deploy-check.toml'


class DeployCheckConfigTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = tomllib.loads(DEPLOY_CHECK_PATH.read_text(encoding='utf-8'))

    def _checks(self, level):
        return self.config['checks'][level]

    def _expectations_for(self, level, path_suffix):
        return [
            check for check in self._checks(level)
            if check['url'].endswith(path_suffix)
        ]

    def test_migration_check_command_is_read_only(self):
        """未適用 migration の確認は read-only。deploy-watch が誤って適用しないこと。"""
        check_command = self.config['migrations']['check_command']
        self.assertIn('showmigrations', check_command)
        self.assertNotIn('migrate --noinput', check_command)
        self.assertNotIn('|migrate|', check_command)

    def test_migration_apply_command_creates_job_first(self):
        """Job 不在でも適用できるよう、作成スクリプトを経由する。"""
        apply_command = self.config['migrations']['apply_command']
        self.assertIn('scripts/create_migrate_job.sh', apply_command)
        self.assertIn('gcloud run jobs execute vrc-ta-hub-migrate', apply_command)

    def test_top_page_is_critical(self):
        """トップページ 500（今回の事故）を critical で検知する。"""
        checks = self._expectations_for('critical', 'vrc-ta-hub.com/')
        self.assertTrue(checks)
        self.assertTrue(all(check['expect_status'] == 200 for check in checks))

    def test_health_check_asserts_db_and_cache(self):
        """/health は cache 失敗でも status=ok を返すため、db と cache の値まで見る。

        DatabaseCache のテーブル未作成（migration 未適用）を status だけでは検知できない。
        """
        bodies = {
            check['expect_body']
            for check in self._expectations_for('critical', '/health')
        }
        self.assertIn('"db": "ok"', bodies)
        self.assertIn('"cache": "ok"', bodies)
        self.assertIn('"status": "ok"', bodies)

    def test_important_checks_cover_login_and_listings(self):
        important_urls = [check['url'] for check in self._checks('important')]
        for path in ('/account/login/', '/community/list/', '/event/detail/history/'):
            self.assertTrue(
                any(url.endswith(path) for url in important_urls),
                f'{path} should be an important check',
            )
        self.assertTrue(
            all(check['expect_status'] == 200 for check in self._checks('important'))
        )

    def test_check_urls_are_absolute_production_urls(self):
        """deploy-watch は絶対 URL から path を取り出し canary タグ URL へ差し替える。"""
        for level in ('critical', 'important'):
            for check in self._checks(level):
                self.assertTrue(
                    check['url'].startswith('https://vrc-ta-hub.com/'),
                    f"{check['name']}: {check['url']}",
                )
