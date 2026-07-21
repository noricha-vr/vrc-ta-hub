"""live smoke のopt-inとcredential判定を検証する。"""

from unittest.mock import patch

from django.test import SimpleTestCase

from tests.live_smoke import require_live_smoke


class LiveSmokeGateTest(SimpleTestCase):
    def test_dummy_credential_is_skipped_even_when_opted_in(self):
        with patch.dict(
            "os.environ",
            {"RUN_LIVE_SMOKE_TESTS": "1", "EXAMPLE_API_KEY": "dummy-key"},
            clear=True,
        ):
            @require_live_smoke("EXAMPLE_API_KEY")
            def target():
                return None

        self.assertTrue(target.__unittest_skip__)
        self.assertEqual(target.tags, {"external_api", "live_smoke"})

    def test_real_credential_still_requires_explicit_opt_in(self):
        with patch.dict("os.environ", {"EXAMPLE_API_KEY": "real-key"}, clear=True):
            @require_live_smoke("EXAMPLE_API_KEY")
            def target():
                return None

        self.assertTrue(target.__unittest_skip__)

    def test_real_credential_with_opt_in_is_enabled(self):
        with patch.dict(
            "os.environ",
            {"RUN_LIVE_SMOKE_TESTS": "1", "EXAMPLE_API_KEY": "real-key"},
            clear=True,
        ):
            @require_live_smoke("EXAMPLE_API_KEY")
            def target():
                return None

        self.assertFalse(getattr(target, "__unittest_skip__", False))
