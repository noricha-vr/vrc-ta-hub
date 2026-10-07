"""Cloud Build の Cloud Run デプロイ設定テスト。"""

from pathlib import Path

import yaml
from django.test import SimpleTestCase


REPO_ROOT = Path(__file__).resolve().parents[3]
# gcloud の --update-env-vars で区切り文字を '|' に替える接頭辞（値にカンマを含めるため）
ENV_VARS_DELIMITER_PREFIX = '^|^'


class CloudBuildConfigTest(SimpleTestCase):
    def setUp(self):
        self.cloudbuild = (REPO_ROOT / 'cloudbuild.yaml').read_text()

    def test_production_deploy_does_not_auto_assign_traffic_tag(self):
        """Cloud Build はカナリアタグ付与を行わない。タグ運用は deploy-watch に集約する。

        参照: ~/.claude/skills/deploy-watch/SKILL.md
        """
        # 自動付与は preview / canary / smoke / candidate いずれも禁止
        self.assertNotIn("--update-tags='preview=LATEST'", self.cloudbuild)
        self.assertNotIn("--update-tags='canary=LATEST'", self.cloudbuild)
        self.assertNotIn("--update-tags=canary=LATEST", self.cloudbuild)
        self.assertNotIn("--update-tags='smoke=LATEST'", self.cloudbuild)
        self.assertNotIn("--update-tags='candidate=LATEST'", self.cloudbuild)

    def test_production_deploy_does_not_assign_tag_during_deploy(self):
        self.assertNotIn("'--tag'", self.cloudbuild)
        self.assertNotIn("rev-$SHORT_SHA", self.cloudbuild)

    def test_production_deploy_cleans_up_legacy_rev_tags(self):
        """旧 `rev-*` タグの掃除処理は維持する（残骸タグ削減のため）。"""
        self.assertIn("grep '^rev-'", self.cloudbuild)
        self.assertIn("--remove-tags", self.cloudbuild)

    def test_cloud_run_memory_limit_is_1gib(self):
        """記事生成時のPDF処理に備えCloud Runメモリ上限を1GiBにする."""
        self.assertIn("'--memory'", self.cloudbuild)
        self.assertIn("'1Gi'", self.cloudbuild)
        self.assertNotIn("'512Mi'", self.cloudbuild)

    def _production_deploy_arg(self, flag: str) -> str:
        """本番の `gcloud run deploy` ステップで flag の直後に渡す値を返す。"""
        steps = yaml.safe_load(self.cloudbuild)['steps']
        deploy_args = next(step['args'] for step in steps if step.get('args', [])[:2] == ['run', 'deploy'])
        return deploy_args[deploy_args.index(flag) + 1]

    def test_production_deploy_passes_turnstile_keys(self):
        """Turnstile のサイトキー（公開値）は環境変数、シークレットキーは Secret Manager から渡す。"""
        secrets = self._production_deploy_arg('--set-secrets').split(',')
        env_vars_arg = self._production_deploy_arg('--update-env-vars')
        self.assertTrue(env_vars_arg.startswith(ENV_VARS_DELIMITER_PREFIX))
        env_vars = env_vars_arg.removeprefix(ENV_VARS_DELIMITER_PREFIX).split('|')

        self.assertIn('TURNSTILE_SECRET_KEY=TURNSTILE_SECRET_KEY:latest', secrets)
        self.assertIn('TURNSTILE_SITE_KEY=0x4AAAAAAFIx-kaCNvnLqGXv', env_vars)
        self.assertFalse(any(item.startswith('TURNSTILE_SECRET_KEY=') for item in env_vars))

    def test_cloud_build_does_not_run_django_migrations(self):
        """Cloud Build は Django migration を自動実行しない。

        本番 schema の変更は人間が影響を確認し、デプロイ前に手動で適用する。
        判断記録: docs/research/issue-464-cloud-run-job-migration.md
        """
        self.assertNotIn('manage.py,migrate', self.cloudbuild)
        self.assertNotIn('manage.py migrate', self.cloudbuild)
        self.assertNotIn('jobs execute vrc-ta-hub-migrate', self.cloudbuild)
        self.assertNotIn('jobs deploy vrc-ta-hub-migrate', self.cloudbuild)
