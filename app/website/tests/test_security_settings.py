import website.settings as project_settings

from django.conf import settings
from django.test import SimpleTestCase


class SecuritySettingsTest(SimpleTestCase):
    def test_cors_is_restricted(self):
        self.assertFalse(settings.CORS_ALLOW_ALL_ORIGINS)
        self.assertIn('https://vrc-ta-hub.com', settings.CORS_ALLOWED_ORIGINS)

    def test_secure_cookie_and_hsts_settings_follow_debug(self):
        expected_secure = not project_settings.DEBUG

        self.assertEqual(settings.SESSION_COOKIE_SECURE, expected_secure)
        self.assertEqual(settings.CSRF_COOKIE_SECURE, expected_secure)
        self.assertEqual(settings.SECURE_HSTS_SECONDS, 31536000 if expected_secure else 0)
        self.assertEqual(settings.SECURE_HSTS_INCLUDE_SUBDOMAINS, expected_secure)
        self.assertEqual(settings.SECURE_HSTS_PRELOAD, expected_secure)

    def test_security_headers_are_enabled(self):
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)
        self.assertEqual(settings.SECURE_REFERRER_POLICY, 'strict-origin-when-cross-origin')
