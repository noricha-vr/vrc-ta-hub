"""docs/deploy-check.toml（deploy-watch が読むデプロイ前チェック定義）のテスト。"""

import importlib.util
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
        """未適用 migration の確認は read-only。deploy-watch が誤って適用しないこと。

        check_command 自体はスクリプトなので、スクリプト本体が showmigrations だけを
        実行し migrate を含まないことまで見る。
        """
        check_command = self.config['migrations']['check_command']
        self.assertIn('check_pending_migrations.sh', check_command)

        script = REPO_ROOT / 'scripts' / 'check_pending_migrations.sh'
        body = script.read_text(encoding='utf-8')
        self.assertIn('showmigrations', body)
        # 実行対象として migrate を渡す箇所が無いこと（復元用の args 文字列は除く）
        self.assertNotIn("--args='^|^manage.py|migrate", body)

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

    def test_health_check_asserts_shared_cache(self):
        """/health は cache 失敗でも status=ok を返すため、各フィールドの値まで見る。

        cache フィールドは常に LocMem の healthcheck alias を見るので、
        DatabaseCache のテーブル未作成（migration 未適用）は shared_cache でしか
        検知できない。これが無いと、この設定ファイルを追加した意味が無くなる。
        """
        bodies = {
            check['expect_body']
            for check in self._expectations_for('critical', '/health')
        }
        self.assertIn('"db": "ok"', bodies)
        self.assertIn('"shared_cache": "ok"', bodies)
        self.assertIn('"status": "ok"', bodies)

    def test_login_page_is_critical(self):
        """ログイン画面は DatabaseCache に依存するので即時モードでも検証する

        important は --immediate でスキップされる。緊急デプロイでこそ
        migration 未適用を検知したいので critical に置く。
        """
        critical_urls = [check['url'] for check in self._checks('critical')]
        self.assertTrue(
            any(url.endswith('/account/login/') for url in critical_urls),
            '/account/login/ should be a critical check',
        )

    def test_important_checks_cover_listings(self):
        important_urls = [check['url'] for check in self._checks('important')]
        for path in ('/community/list/', '/event/detail/history/'):
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


class ReadDeployCheckTest(SimpleTestCase):
    """scripts/read_deploy_check.py（deploy-watch が読む要約層）のテスト。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        script_path = REPO_ROOT / 'scripts' / 'read_deploy_check.py'
        spec = importlib.util.spec_from_file_location('read_deploy_check', script_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f'could not load {script_path}')
        cls.reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.reader)
        cls.config = tomllib.loads(DEPLOY_CHECK_PATH.read_text(encoding='utf-8'))

    def test_actual_config_passes_validation(self):
        """実際の deploy-check.toml が検証を通る（スキーマ違反の早期検知）。"""
        summary = self.reader.build_summary(self.config, base_dir=REPO_ROOT)

        self.assertEqual(summary['service'], 'vrc-ta-hub')
        self.assertTrue(summary['selected_checks']['critical'])

    def test_summary_includes_migrations(self):
        """migrations は要約に含める。読み捨てるとこの設定ファイルの主目的が失われる。"""
        summary = self.reader.build_summary(self.config, base_dir=REPO_ROOT)

        self.assertIn('migrations', summary)
        self.assertIn('check_pending_migrations.sh', summary['migrations']['check_command'])
        self.assertTrue(summary['migrations']['apply_command'])

    def test_migrations_section_is_required(self):
        config = {key: value for key, value in self.config.items() if key != 'migrations'}

        with self.assertRaises(ValueError):
            self.reader.build_summary(config)

    def _with_check_command(self, command):
        config = dict(self.config)
        config['migrations'] = dict(self.config['migrations'], check_command=command)
        return config

    def test_check_command_running_migrate_is_rejected(self):
        """確認のつもりで適用してしまう定義を実行前に落とす。

        シェルの区切りは `;` `&&` `(` など多様で、許可する区切りを列挙する方式では
        取りこぼす。区切りに依存せず検知できることを確かめる。
        """
        for command in (
            'python manage.py migrate',
            './scripts/check_pending_migrations.sh;python manage.py migrate',
            './scripts/check_pending_migrations.sh && python manage.py migrate',
            '(cd app && python manage.py migrate)',
            "gcloud run jobs update x --args='^|^manage.py|migrate|--noinput'",
        ):
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    self.reader.build_summary(self._with_check_command(command))

    def test_check_command_executing_job_is_rejected(self):
        """Job 名が -migrate で終わる形は migrate 検知をすり抜けるため別に落とす。"""
        command = (
            'gcloud run jobs execute vrc-ta-hub-migrate '
            '--region=asia-northeast1 --project=vrc-ta-hub --wait'
        )

        with self.assertRaises(ValueError):
            self.reader.build_summary(self._with_check_command(command))

    def test_read_only_commands_are_not_rejected(self):
        """read-only なコマンドを migrate と誤検知しない。"""
        for command in (
            './scripts/check_pending_migrations.sh',
            'python manage.py showmigrations --plan',
            'python manage.py sqlmigrate user_account 0016',
        ):
            with self.subTest(command=command):
                summary = self.reader.build_summary(self._with_check_command(command))

                self.assertEqual(summary['migrations']['check_command'], command)

    def test_missing_referenced_script_is_rejected(self):
        config = dict(self.config)
        config['migrations'] = dict(
            self.config['migrations'], check_command='./scripts/nonexistent.sh'
        )

        with self.assertRaises(ValueError):
            self.reader.build_summary(config, base_dir=REPO_ROOT)

    def test_script_outside_repository_is_rejected(self):
        """リポジトリ外を指す参照は、実在しても検証の対象外なので落とす。"""
        config = dict(self.config)
        config['migrations'] = dict(
            self.config['migrations'], check_command='./scripts/../../../tmp/evil.sh'
        )

        with self.assertRaises(ValueError):
            self.reader.build_summary(config, base_dir=REPO_ROOT)

    def test_render_text_shows_migrations(self):
        """テキスト出力にも migrations を出す（AI・人間が目視する経路）。"""
        summary = self.reader.build_summary(self.config, base_dir=REPO_ROOT)

        rendered = self.reader.render_text(summary)

        self.assertIn('migrations:', rendered)
        self.assertIn('check_pending_migrations.sh', rendered)
