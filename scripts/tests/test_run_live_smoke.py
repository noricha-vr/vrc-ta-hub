#!/usr/bin/env python3
"""run_live_smoke.pyのcredential境界を検証する。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from scripts.run_live_smoke import (
    LiveSmokeConfigurationError,
    build_execution,
)


class LiveSmokeExecutionTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_root = Path(self.temp_dir.name)
        (self.repo_root / "app").mkdir()
        self.base_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": self.temp_dir.name,
        }

    def test_openrouter_passes_only_allowlisted_credential_without_secret_argv(self):
        environ = {
            **self.base_env,
            "OPENROUTER_API_KEY": "real-openrouter-secret",
            "GOOGLE_API_KEY": "real-google-secret",
            "UNRELATED_SECRET": "must-not-leak",
        }

        command, child_env = build_execution(
            "openrouter",
            (),
            environ=environ,
            repo_root=self.repo_root,
        )

        self.assertEqual(child_env["RUN_LIVE_SMOKE_TESTS"], "1")
        self.assertEqual(child_env["OPENROUTER_API_KEY"], "real-openrouter-secret")
        self.assertNotIn("GOOGLE_API_KEY", child_env)
        self.assertNotIn("UNRELATED_SECRET", child_env)
        self.assertNotIn("real-openrouter-secret", command)
        self.assertIn("--no-deps", command)
        self.assertIn("--build", command)
        self.assertIn("live-smoke", command)
        self.assertNotIn("exec", command)
        self.assertFalse(any("test_get_transcript" in item for item in command))
        self.assertFalse(any("test_google_calendar" in item for item in command))
        self.assertFalse(any("test_generate_blog_video" in item for item in command))

    def test_blog_generation_passes_only_its_two_required_keys(self):
        environ = {
            **self.base_env,
            "OPENROUTER_API_KEY": "real-openrouter-secret",
            "GOOGLE_API_KEY": "real-google-secret",
            "GOOGLE_CALENDAR_ID": "must-not-leak",
        }

        command, child_env = build_execution(
            "blog-generation",
            (),
            environ=environ,
            repo_root=self.repo_root,
        )

        self.assertEqual(child_env["OPENROUTER_API_KEY"], "real-openrouter-secret")
        self.assertEqual(child_env["GOOGLE_API_KEY"], "real-google-secret")
        self.assertNotIn("GOOGLE_CALENDAR_ID", child_env)
        self.assertTrue(any("test_generate_blog_video_and_pdf" in item for item in command))
        self.assertFalse(any("test_generate_blog_pdf_only" in item for item in command))

    def test_env_file_reads_only_profile_allowlist(self):
        env_file = self.repo_root / ".env.local"
        env_file.write_text(
            "OPENROUTER_API_KEY='real-from-file'\n"
            "GOOGLE_API_KEY=real-google-secret\n"
            "UNRELATED_SECRET=must-not-leak\n",
            encoding="utf-8",
        )

        command, child_env = build_execution(
            "openrouter",
            (),
            environ=self.base_env,
            repo_root=self.repo_root,
        )

        self.assertEqual(child_env["OPENROUTER_API_KEY"], "real-from-file")
        self.assertNotIn("GOOGLE_API_KEY", child_env)
        self.assertNotIn("UNRELATED_SECRET", child_env)
        self.assertNotIn("real-from-file", command)

    def test_google_calendar_mounts_only_configured_credential_file(self):
        credential_path = self.repo_root / "app" / "secret" / "credentials.json"
        credential_path.parent.mkdir()
        credential_path.write_text("{}", encoding="utf-8")
        environ = {
            **self.base_env,
            "GOOGLE_CALENDAR_ID": "real-calendar-id",
            "GOOGLE_CALENDAR_CREDENTIALS": "/app/secret/credentials.json",
            "OPENROUTER_API_KEY": "must-not-leak",
        }

        command, child_env = build_execution(
            "google-calendar",
            (),
            environ=environ,
            repo_root=self.repo_root,
        )

        mount = f"{credential_path.resolve()}:/run/secrets/google-calendar-credentials.json:ro"
        self.assertIn(mount, command)
        self.assertEqual(
            child_env["GOOGLE_CALENDAR_CREDENTIALS"],
            "/run/secrets/google-calendar-credentials.json",
        )
        self.assertNotIn("OPENROUTER_API_KEY", child_env)
        self.assertNotIn("real-calendar-id", command)

    def test_unknown_profile_is_rejected(self):
        with self.assertRaisesRegex(LiveSmokeConfigurationError, "unknown live smoke profile"):
            build_execution(
                "unknown",
                (),
                environ=self.base_env,
                repo_root=self.repo_root,
            )

    def test_missing_or_dummy_credential_is_rejected(self):
        for value in ("", "dummy-openrouter", "test-openrouter", "placeholder-key"):
            with self.subTest(value=value):
                environ = {**self.base_env, "OPENROUTER_API_KEY": value}
                with self.assertRaisesRegex(
                    LiveSmokeConfigurationError,
                    "missing or dummy credential",
                ):
                    build_execution(
                        "openrouter",
                        (),
                        environ=environ,
                        repo_root=self.repo_root,
                    )

    def test_missing_calendar_credential_file_is_rejected(self):
        environ = {
            **self.base_env,
            "GOOGLE_CALENDAR_ID": "real-calendar-id",
            "GOOGLE_CALENDAR_CREDENTIALS": "/app/secret/missing.json",
        }
        with self.assertRaisesRegex(
            LiveSmokeConfigurationError,
            "credential file does not exist",
        ):
            build_execution(
                "google-calendar",
                (),
                environ=environ,
                repo_root=self.repo_root,
            )


if __name__ == "__main__":
    unittest.main()
