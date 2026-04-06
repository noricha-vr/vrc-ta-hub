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
        self.assertNotIn("'--tag'", self.cloudbuild)


class PullRequestTemplateTest(SimpleTestCase):
    def setUp(self):
        self.template_path = REPO_ROOT / '.github' / 'pull_request_template.md'
        self.template = self.template_path.read_text() if self.template_path.exists() else ''

    def test_pull_request_template_exists(self):
        self.assertTrue(self.template_path.exists())

    def test_pull_request_template_includes_required_sections(self):
        self.assertIn('## なぜこの変更が必要か', self.template)
        self.assertIn('## 変更概要', self.template)
        self.assertIn('## テスト', self.template)
        self.assertIn('## チェックリスト', self.template)
