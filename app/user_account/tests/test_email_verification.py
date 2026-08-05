"""Email verification flows exposed by the local account views."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import resolve, reverse

from allauth.account.models import EmailAddress, EmailConfirmationHMAC

from tests.factories import make_user, make_user_without_email_address
from user_account.adapters import CustomAccountAdapter
from user_account.tests.utils import create_discord_linked_user
from user_account.tests.utils import TEST_SOCIALACCOUNT_PROVIDERS
from user_account.views import CustomLoginView

User = get_user_model()


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EmailVerificationViewTests(TestCase):
    """Verify local login and pending email-change behavior."""

    def setUp(self):
        self.client = Client()

    def test_api_auth_login_uses_custom_login_view(self):
        """Route the DRF login path through mandatory verification."""
        self.assertEqual(resolve('/api-auth/login/').func.view_class, CustomLoginView)

    def test_createsuperuser_has_verified_primary_email(self):
        """Keep trusted CLI-created administrators usable after migration."""
        user = User.objects.create_superuser(
            user_name='trusted_admin',
            email='trusted-admin@example.com',
            password='testpass123',
        )

        self.assertTrue(EmailAddress.objects.filter(
            user=user,
            email=user.email,
            verified=True,
            primary=True,
        ).exists())

    def test_admin_login_blocks_unverified_staff(self):
        """Prevent Django admin from bypassing mandatory verification."""
        user = make_user(
            user_name='pending_staff',
            email='pending-staff@example.com',
            password='testpass123',
            is_staff=True,
        )
        EmailAddress.objects.filter(user=user).update(verified=False)

        response = self.client.post(reverse('admin:login'), {
            'username': user.email,
            'password': 'testpass123',
            'next': reverse('admin:index'),
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'メールアドレスの確認を完了してください')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_admin_created_user_has_verified_primary_email(self):
        """Treat an authenticated operator's admin creation as trusted."""
        admin = User.objects.create_superuser(
            user_name='creating_admin',
            email='creating-admin@example.com',
            password='testpass123',
        )
        self.client.force_login(admin)

        response = self.client.post(reverse('admin:user_account_customuser_add'), {
            'email': 'admin-created@example.com',
            'user_name': 'admin_created',
            'display_name': 'Admin Created',
            'password1': 'testpass12345',
            'password2': 'testpass12345',
            '_save': '保存',
        })

        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email='admin-created@example.com')
        self.assertTrue(EmailAddress.objects.filter(
            user=user,
            email=user.email,
            verified=True,
            primary=True,
        ).exists())

    def test_admin_creation_rejects_another_users_pending_email(self):
        """Prevent trusted creation from claiming a pending email identity."""
        admin = User.objects.create_superuser(
            user_name='pending_collision_admin',
            email='pending-collision-admin@example.com',
            password='testpass123',
        )
        owner = make_user(
            user_name='pending_owner',
            email='pending-owner@example.com',
        )
        EmailAddress.objects.create(
            user=owner,
            email='claimed-pending@example.com',
            verified=False,
            primary=False,
        )
        self.client.force_login(admin)

        response = self.client.post(reverse('admin:user_account_customuser_add'), {
            'email': 'claimed-pending@example.com',
            'user_name': 'rejected_admin_created',
            'display_name': 'Rejected',
            'password1': 'testpass12345',
            'password2': 'testpass12345',
            '_save': '保存',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'このメールアドレスは既に登録されています')
        self.assertFalse(User.objects.filter(user_name='rejected_admin_created').exists())

    def test_createsuperuser_rejects_another_users_pending_email(self):
        """Prevent the trusted CLI path from claiming a pending email identity."""
        owner = make_user(
            user_name='cli_pending_owner',
            email='cli-pending-owner@example.com',
        )
        EmailAddress.objects.create(
            user=owner,
            email='cli-claimed-pending@example.com',
            verified=False,
            primary=False,
        )

        with self.assertRaisesRegex(ValueError, '既に登録されています'):
            User.objects.create_superuser(
                user_name='rejected_cli_admin',
                email='cli-claimed-pending@example.com',
                password='testpass123',
            )

        self.assertFalse(User.objects.filter(user_name='rejected_cli_admin').exists())

    def test_admin_email_change_stays_pending_until_confirmation(self):
        """Apply the staged email-change contract to Django admin edits too."""
        admin = User.objects.create_superuser(
            user_name='changing_admin',
            email='changing-admin@example.com',
            password='testpass123',
        )
        target = make_user(
            user_name='admin_change_target',
            email='admin-change-old@example.com',
            password='testpass123',
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse('admin:user_account_customuser_change', args=[target.pk]),
            {
                'email': 'admin-change-new@example.com',
                'user_name': target.user_name,
                'display_name': target.display_name,
                'vrchat_user_id': '',
                'password': target.password,
                'is_active': 'on',
                'date_joined_0': target.date_joined.strftime('%Y-%m-%d'),
                'date_joined_1': target.date_joined.strftime('%H:%M:%S'),
                '_save': '保存',
            },
        )

        self.assertEqual(response.status_code, 302)
        target.refresh_from_db()
        self.assertEqual(target.email, 'admin-change-old@example.com')
        self.assertTrue(EmailAddress.objects.filter(
            user=target,
            email='admin-change-new@example.com',
            verified=False,
            primary=False,
        ).exists())
        self.assertEqual(len(mail.outbox), 1)

    def test_admin_email_change_mail_failure_keeps_old_login(self):
        """Show an operator warning while leaving the staged change retryable."""
        admin = User.objects.create_superuser(
            user_name='mail_failure_admin',
            email='mail-failure-admin@example.com',
            password='testpass123',
        )
        target = make_user(
            user_name='admin_mail_failure_target',
            email='admin-mail-failure-old@example.com',
        )
        self.client.force_login(admin)

        with patch.object(
            CustomAccountAdapter,
            'send_mail',
            side_effect=RuntimeError('simulated mail failure'),
        ):
            response = self.client.post(
                reverse('admin:user_account_customuser_change', args=[target.pk]),
                {
                    'email': 'admin-mail-failure-new@example.com',
                    'user_name': target.user_name,
                    'display_name': target.display_name,
                    'vrchat_user_id': '',
                    'password': target.password,
                    'is_active': 'on',
                    'date_joined_0': target.date_joined.strftime('%Y-%m-%d'),
                    'date_joined_1': target.date_joined.strftime('%H:%M:%S'),
                    '_save': '保存',
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '確認メールを送信できませんでした')
        target.refresh_from_db()
        self.assertEqual(target.email, 'admin-mail-failure-old@example.com')
        self.assertTrue(EmailAddress.objects.filter(
            user=target,
            email='admin-mail-failure-new@example.com',
            verified=False,
            primary=False,
        ).exists())

    def test_api_auth_login_blocks_unverified_email(self):
        """Apply the same mandatory verification gate to DRF's login path."""
        user = make_user(
            user_name='pending_api_user',
            email='pending-api@example.com',
            password='testpass123',
        )
        EmailAddress.objects.filter(user=user).update(verified=False)

        response = self.client.post('/api-auth/login/', {
            'username': user.email, 'password': 'testpass123',
        })

        self.assertRedirects(response, '/accounts/confirm-email/', fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_unverified_login_without_remember_expires_when_browser_closes(self):
        """Preserve remember-off after email confirmation resumes the login."""
        user = make_user(
            user_name='pending_session_user',
            email='pending-session@example.com',
            password='testpass123',
        )
        address = EmailAddress.objects.get(user=user)
        address.verified = False
        address.save(update_fields=['verified'])

        response = self.client.post(reverse('account:login'), {
            'username': user.email,
            'password': 'testpass123',
        })

        self.assertRedirects(response, '/accounts/confirm-email/', fetch_redirect_response=False)
        confirmation_url = reverse(
            'account_confirm_email',
            args=[EmailConfirmationHMAC(address).key],
        )
        self.client.get(confirmation_url)
        self.client.post(confirmation_url)

        self.assertIn('_auth_user_id', self.client.session)
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_login_syncs_missing_email_address_as_unverified(self):
        """Send verification instead of locking a programmatically created user out."""
        user = make_user_without_email_address(
            user_name='missing_address_user',
            email='missing-address@example.com',
            password='testpass123',
        )
        self.assertFalse(EmailAddress.objects.filter(user=user).exists())

        response = self.client.post(reverse('account:login'), {
            'username': user.email,
            'password': 'testpass123',
        })

        self.assertRedirects(
            response,
            '/accounts/confirm-email/',
            fetch_redirect_response=False,
        )
        self.assertTrue(EmailAddress.objects.filter(
            user=user,
            email=user.email,
            verified=False,
            primary=False,
        ).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_api_auth_login_allows_verified_email(self):
        """Keep DRF's session login available to verified users."""
        user = create_discord_linked_user(
            user_name='verified_api_user',
            email='verified-api@example.com',
            password='testpass123',
        )

        response = self.client.post('/api-auth/login/', {
            'username': user.email,
            'password': 'testpass123',
            'next': '/api/v1/event-details/',
        })

        self.assertRedirects(response, '/api/v1/event-details/', fetch_redirect_response=False)
        self.assertEqual(self.client.session['_auth_user_id'], str(user.pk))

    @override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS)
    def test_local_registration_confirmation_enables_login(self):
        """Confirm the registration address through allauth before allowing login."""
        response = self.client.post(reverse('account:register'), {
            'user_name': 'confirmed_signup_user',
            'email': 'confirmed-signup@example.com',
            'password1': 'testpass12345',
            'password2': 'testpass12345',
        })
        self.assertRedirects(response, '/accounts/confirm-email/', fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)

        user = User.objects.get(email='confirmed-signup@example.com')
        address = EmailAddress.objects.get(user=user, email=user.email)
        self.assertFalse(address.verified)

        confirmation_url = reverse(
            'account_confirm_email',
            args=[EmailConfirmationHMAC(address).key],
        )
        self.client.get(confirmation_url)
        self.client.post(confirmation_url)
        address.refresh_from_db()
        self.assertTrue(address.verified)

        self.client.logout()
        response = self.client.post(reverse('account:login'), {
            'username': user.email,
            'password': 'testpass12345',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session['_auth_user_id'], str(user.pk))

    def test_unverified_login_resends_confirmation_without_authenticating(self):
        """Keep an unverified user logged out until their address is confirmed."""
        user = make_user(
            user_name='pending_login_user',
            email='pending-login@example.com',
            password='testpass123',
        )
        address = EmailAddress.objects.get(user=user)
        address.verified = False
        address.save(update_fields=['verified'])

        response = self.client.post(reverse('account:login'), {
            'username': user.email, 'password': 'testpass123',
        })

        self.assertRedirects(response, '/accounts/confirm-email/', fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(len(mail.outbox), 1)

        address.set_verified()
        response = self.client.post(reverse('account:login'), {
            'username': user.email, 'password': 'testpass123',
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('_auth_user_id', self.client.session)

    def test_email_change_stays_pending_until_confirmation(self):
        """Keep the old primary address until allauth confirms the replacement."""
        user = create_discord_linked_user(
            user_name='test_update_user',
            email='test_update@example.com',
            password='testpass123',
        )
        self.client.force_login(user)

        response = self.client.post(reverse('account:user_update'), {
            'display_name': '更新済み表示名',
            'user_name': user.user_name,
            'email': 'new-update@example.com',
            'x_account': '',
            'vrchat_user_id': '',
        })

        self.assertRedirects(response, reverse('account:settings'), fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertEqual(user.email, 'test_update@example.com')
        self.assertTrue(EmailAddress.objects.filter(
            user=user, email=user.email, verified=True, primary=True,
        ).exists())
        self.assertTrue(EmailAddress.objects.filter(
            user=user, email='new-update@example.com', verified=False, primary=False,
        ).exists())
        self.assertEqual(len(mail.outbox), 1)

        pending = EmailAddress.objects.get(user=user, email='new-update@example.com')
        confirmation_url = reverse('account_confirm_email', args=[EmailConfirmationHMAC(pending).key])
        self.client.get(confirmation_url)
        self.client.post(confirmation_url)

        user.refresh_from_db()
        self.assertEqual(user.email, 'new-update@example.com')
        self.assertTrue(EmailAddress.objects.filter(
            user=user, email=user.email, verified=True, primary=True,
        ).exists())
        self.assertFalse(EmailAddress.objects.filter(
            user=user, email='test_update@example.com',
        ).exists())

    def test_email_change_mail_failure_keeps_old_login_and_allows_retry(self):
        """Keep profile updates usable while an email delivery failure stays retryable."""
        user = create_discord_linked_user(
            user_name='mail_failure_user',
            email='mail-failure@example.com',
            password='testpass123',
        )
        self.client.force_login(user)

        with patch.object(
            CustomAccountAdapter,
            'send_mail',
            side_effect=RuntimeError('simulated mail failure'),
        ):
            response = self.client.post(reverse('account:user_update'), {
                'display_name': '送信失敗後も保存',
                'user_name': user.user_name,
                'email': 'mail-failure-new@example.com',
                'x_account': '',
                'vrchat_user_id': '',
            })

        self.assertRedirects(
            response,
            reverse('account:settings'),
            fetch_redirect_response=False,
        )
        user.refresh_from_db()
        self.assertEqual(user.display_name, '送信失敗後も保存')
        self.assertEqual(user.email, 'mail-failure@example.com')
        self.assertTrue(EmailAddress.objects.filter(
            user=user,
            email='mail-failure-new@example.com',
            verified=False,
            primary=False,
        ).exists())

        cache.clear()
        response = self.client.post(reverse('account:user_update'), {
            'display_name': user.display_name,
            'user_name': user.user_name,
            'email': 'mail-failure-new@example.com',
            'x_account': '',
            'vrchat_user_id': '',
        })

        self.assertRedirects(
            response,
            reverse('account:settings'),
            fetch_redirect_response=False,
        )
        self.assertEqual(len(mail.outbox), 1)
