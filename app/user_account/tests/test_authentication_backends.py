"""AUTHENTICATION_BACKENDS の構成に関するテスト."""
from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.test import TestCase, tag
from django.urls import reverse

from tests.factories import make_user

ALLAUTH_BACKEND = 'allauth.account.auth_backends.AuthenticationBackend'
LEGACY_MODEL_BACKEND = 'django.contrib.auth.backends.ModelBackend'


@tag('offline_external_api')
class AuthenticationBackendsTests(TestCase):
    """email ログイン移行後の後片付け（#598）の回帰テスト."""

    def setUp(self):
        self.user = make_user(
            user_name='backend_user', email='backend@example.com', password='testpass12345',
        )

    def _login_with_backend(self, backend):
        session = self.client.session
        session[SESSION_KEY] = str(self.user.pk)
        session[BACKEND_SESSION_KEY] = backend
        session[HASH_SESSION_KEY] = self.user.get_session_auth_hash()
        session.save()

    def test_only_allauth_backend_is_configured(self):
        self.assertEqual(settings.AUTHENTICATION_BACKENDS, [ALLAUTH_BACKEND])

    def test_session_with_legacy_backend_is_treated_as_anonymous(self):
        """撤去した ModelBackend を指す旧セッションは 500 にならず未ログイン扱いになること."""
        self._login_with_backend(LEGACY_MODEL_BACKEND)

        response = self.client.get(reverse('account:settings'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('account:login'), response['Location'])

    def test_session_with_allauth_backend_stays_logged_in(self):
        self._login_with_backend(ALLAUTH_BACKEND)

        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.wsgi_request.user, self.user)

    def test_permission_check_still_works_via_allauth_backend(self):
        """allauth backend が ModelBackend 相当の権限判定を担うこと."""
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])

        self.assertTrue(self.user.has_perm('user_account.change_customuser'))
