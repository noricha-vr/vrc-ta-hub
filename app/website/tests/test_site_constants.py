import importlib
import os
from unittest.mock import patch

from django.test import SimpleTestCase

import website.constants as site_constants


class SiteConstantsTest(SimpleTestCase):
    def tearDown(self):
        importlib.reload(site_constants)

    def test_build_site_url_handles_relative_and_absolute_paths(self):
        module = importlib.reload(site_constants)

        self.assertEqual(module.build_site_url("/community/1/"), "https://vrc-ta-hub.com/community/1/")
        self.assertEqual(module.build_site_url("event/detail/2/"), "https://vrc-ta-hub.com/event/detail/2/")
        self.assertEqual(module.build_site_url("https://example.com/page"), "https://example.com/page")
        self.assertEqual(module.build_site_url("//cdn.example.com/image.png"), "https://cdn.example.com/image.png")

    def test_constants_read_site_and_openrouter_environment(self):
        env = {
            "SITE_DOMAIN": "preview.example.com",
            "SITE_URL": "https://preview.example.com/base/",
            "OPENROUTER_BASE_URL": "https://router.example.com/api/v1/",
            "DEFAULT_NEWS_IMAGE_URL": "https://assets.example.com/default.jpg",
        }
        with patch.dict(os.environ, env, clear=False):
            module = importlib.reload(site_constants)

        self.assertEqual(module.SITE_DOMAIN, "preview.example.com")
        self.assertEqual(module.SITE_URL, "https://preview.example.com/base")
        self.assertEqual(module.OPENROUTER_BASE_URL, "https://router.example.com/api/v1")
        self.assertEqual(module.DEFAULT_NEWS_IMAGE_URL, "https://assets.example.com/default.jpg")

    def test_is_site_domain_matches_exact_and_subdomain_only(self):
        env = {"SITE_DOMAIN": "vrc-ta-hub.com"}
        with patch.dict(os.environ, env, clear=False):
            module = importlib.reload(site_constants)

        self.assertTrue(module.is_site_domain("vrc-ta-hub.com"))
        self.assertTrue(module.is_site_domain("data.vrc-ta-hub.com"))
        self.assertFalse(module.is_site_domain("evilvrc-ta-hub.com"))
        self.assertFalse(module.is_site_domain("example.com"))
