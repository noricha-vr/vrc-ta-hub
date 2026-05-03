"""Cloud Build の Cloud Run デプロイ設定テスト。"""

from pathlib import Path

from django.test import SimpleTestCase


REPO_ROOT = Path(__file__).resolve().parents[3]


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
