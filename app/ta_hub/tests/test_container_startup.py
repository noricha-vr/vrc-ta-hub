from pathlib import Path
import unittest


class ContainerStartupConfigTests(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[3]

    def test_dockerfile_uses_entrypoint_script(self):
        dockerfile = (self.repo_root / "Dockerfile").read_text()

        self.assertIn("COPY docker-entrypoint.sh /docker-entrypoint.sh", dockerfile)
        self.assertIn('CMD ["/docker-entrypoint.sh"]', dockerfile)

    def test_entrypoint_runs_migrate_with_lock_before_supervisord(self):
        entrypoint = (self.repo_root / "docker-entrypoint.sh").read_text()

        self.assertIn("python manage.py migrate_with_lock --noinput", entrypoint)
        self.assertIn(
            "exec supervisord -c /etc/supervisor/supervisord.conf -n",
            entrypoint,
        )
