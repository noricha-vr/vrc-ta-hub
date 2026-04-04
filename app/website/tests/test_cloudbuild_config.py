"""Cloud Build の Cloud Run デプロイ設定テスト。"""

from pathlib import Path

from django.test import SimpleTestCase


REPO_ROOT = Path(__file__).resolve().parents[3]


class CloudBuildConfigTest(SimpleTestCase):
    def setUp(self):
        self.cloudbuild = (REPO_ROOT / 'cloudbuild.yaml').read_text()

    def test_production_deploy_uses_single_preview_tag(self):
        self.assertIn("--update-tags='preview=LATEST'", self.cloudbuild)
        self.assertIn("--remove-tags", self.cloudbuild)
        self.assertNotIn("rev-$SHORT_SHA", self.cloudbuild)

    def test_production_deploy_does_not_assign_tag_during_deploy(self):
        self.assertNotIn("- '--tag'\n", self.cloudbuild)
