"""allauth のアカウント管理画面の表示と既存の送信契約。"""
from unittest.mock import patch

from allauth.account.models import EmailAddress
from bs4 import BeautifulSoup
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from tests.factories import make_community, make_community_member, make_discord_linked_user
from user_account.tests.utils import TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS


@override_settings(
    SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class AllauthAdminUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_discord_linked_user(display_name='主催者サンプル')
        community = make_community(owner=cls.owner)
        cls.member = make_discord_linked_user(user_name='member', email='member@example.com')
        make_community_member(community, cls.member)
        cls.operator = make_discord_linked_user(user_name='operator', email='operator@example.com', is_staff=True)
        cls.superuser = make_discord_linked_user(
            user_name='superuser', email='superuser@example.com', is_staff=True, is_superuser=True,
        )
        cls.personal = make_discord_linked_user(
            user_name='personal', email='personal@example.com', display_name='本人サンプル',
        )

    def setUp(self):
        cache.clear()
        self.client.force_login(self.personal)

    def soup(self, response):
        self.assertEqual(response.status_code, 200)
        return BeautifulSoup(response.content, 'html.parser')

    def test_all_roles_keep_access_to_their_own_account_pages(self):
        pages = [
            ('account_email', 'メールアドレスの変更', 'account/email_change.html'),
            ('account_reauthenticate', '再認証', 'account/reauthenticate.html'),
            ('account_change_password', 'パスワードの変更', 'account/password_change.html'),
            ('account:password_change', 'パスワードの変更', 'account/password_change.html'),
            ('account_set_password', 'パスワードの設定', 'account/password_set.html'),
        ]
        for user in [self.owner, self.member, self.operator, self.superuser, self.personal]:
            for name, title, template in pages:
                with self.subTest(user=user.user_name, page=name):
                    user.set_password('testpass123')
                    if name == 'account_set_password':
                        user.set_unusable_password()
                    user.save(update_fields=['password'])
                    self.client.force_login(user)
                    response = self.client.get(reverse(name))
                    soup = self.soup(response)
                    self.assertTemplateUsed(response, template)
                    self.assertTemplateUsed(response, 'ta_hub/admin_base.html')
                    self.assertEqual(len(soup.select('main h1')), 1)
                    self.assertEqual(soup.select_one('main h1').get_text(strip=True), f'{title}: {user.display_label}')
                    self.assertIn(f'{title} - ', soup.title.get_text())
                    self.assertIsNotNone(soup.select_one('meta[property="og:image"]'))
                    self.assertIsNotNone(soup.select_one('.hub-admin nav[aria-label="パンくず"]'))
                    self.assertNotContains(response, 'confirm(')
                    self.assertNotContains(response, 'testpass123')
                    form = soup.select_one('.hub-admin form input[name="csrfmiddlewaretoken"]').find_parent('form')
                    self.assertEqual(form['method'], 'post')
                    for field in response.context['form']:
                        self.assertIsNotNone(form.select_one(f'[name="{field.name}"]'), field.name)
                    self.assertIsNotNone(form.select_one('button[type="submit"]'))

    def test_anonymous_users_still_require_login(self):
        self.client.logout()
        for name in ['account_email', 'account_set_password', 'account_reauthenticate', 'account_change_password']:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('account:login'), response.url)

    def test_pending_email_keeps_resend_and_cancel_posts_separate_from_add(self):
        pending = EmailAddress.objects.create(user=self.personal, email='pending@example.com', verified=False)
        response = self.client.get(reverse('account_email'))
        soup = self.soup(response)
        form = soup.select_one('#email-change-form')
        self.assertEqual(form['action'], reverse('account_email'))
        self.assertIsNotNone(form.select_one('button[name="action_add"]'))
        self.assertEqual(soup.select_one('#current_email')['value'], self.personal.email)
        self.assertTrue(soup.select_one('#current_email').has_attr('disabled'))
        self.assertEqual(soup.select_one('#new_email')['value'], pending.email)
        self.assertContains(response, '確認待ち')
        self.assertIsNotNone(form.select_one('button[form="pending-email"][name="action_send"]'))
        resend = soup.select_one('form#pending-email')
        panel = soup.select_one('#cancel-email-change')
        self.assertTrue(panel.has_attr('hidden'))
        self.assertIsNone(panel.find_parent('form'))
        cancel = panel.select_one('form')
        for action_form in [resend, cancel]:
            self.assertEqual(action_form['action'], reverse('account_email'))
            self.assertEqual(action_form['method'], 'post')
            self.assertEqual(action_form.select_one('input[name="email"]')['value'], pending.email)
            self.assertIsNotNone(action_form.select_one('input[name="csrfmiddlewaretoken"]'))
        self.assertIsNotNone(cancel.select_one('button[name="action_remove"]'))
        mail.outbox.clear()
        response = self.client.post(reverse('account_email'), {'email': pending.email, 'action_send': ''})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [pending.email])
        self.assertTrue(EmailAddress.objects.filter(pk=pending.pk).exists())
        response = self.client.post(reverse('account_email'), {'email': pending.email, 'action_remove': ''})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(EmailAddress.objects.filter(pk=pending.pk).exists())
        self.personal.refresh_from_db()
        self.assertEqual(self.personal.email, 'personal@example.com')

    def test_pending_without_verified_address_has_no_cancel_action(self):
        self.personal.emailaddress_set.update(verified=False)
        response = self.client.get(reverse('account_email'))
        soup = self.soup(response)
        self.assertIsNotNone(soup.select_one('#new_email'))
        self.assertIsNone(soup.select_one('#current_email'))
        self.assertIsNone(soup.select_one('[name="action_remove"]'))
        self.assertIsNotNone(soup.select_one('[name="action_send"]'))

    def test_email_errors_and_no_pending_state_are_visible(self):
        response = self.client.get(reverse('account_email'))
        soup = self.soup(response)
        self.assertIsNone(soup.select_one('#pending-email'))
        response = self.client.post(reverse('account_email'), {'email': 'invalid-email', 'action_add': ''})
        soup = self.soup(response)
        self.assertIsNotNone(soup.select_one('.admin-form-errors'))
        self.assertEqual(soup.select_one('#id_email')['value'], 'invalid-email')

    def test_account_without_email_shows_empty_state_and_input(self):
        self.personal.emailaddress_set.all().delete()
        self.personal.email = ''
        self.personal.save(update_fields=['email'])
        response = self.client.get(reverse('account_email'))
        soup = self.soup(response)
        self.assertContains(response, 'メールアドレスはまだ登録されていません')
        self.assertIsNotNone(soup.select_one('#email-change-form [name="email"]'))
        self.assertIsNotNone(soup.select_one('#email-change-form button[name="action_add"]'))
        self.assertIsNone(soup.select_one('#current_email'))
        self.assertIsNone(soup.select_one('#new_email'))

    def test_email_change_stays_pending_and_cannot_remove_another_users_email(self):
        response = self.client.post(reverse('account_email'), {'email': 'replacement@example.com', 'action_add': ''})
        self.assertEqual(response.status_code, 302)
        self.personal.refresh_from_db()
        self.assertEqual(self.personal.email, 'personal@example.com')
        self.assertTrue(self.personal.emailaddress_set.filter(email='replacement@example.com', verified=False).exists())
        for user in [self.owner, self.member, self.operator, self.superuser]:
            self.client = Client()
            self.client.force_login(user)
            with self.subTest(user=user.user_name):
                response = self.client.get(reverse('account_email'))
                self.assertNotContains(response, 'replacement@example.com')
                self.client.post(reverse('account_email'), {'email': 'replacement@example.com', 'action_remove': ''})
                self.assertTrue(self.personal.emailaddress_set.filter(email='replacement@example.com').exists())

    def test_password_set_preserves_action_next_and_error_secrecy(self):
        self.personal.set_unusable_password()
        self.personal.save(update_fields=['password'])
        self.client.force_login(self.personal)
        next_url = reverse('account:settings')
        url = reverse('account_set_password') + '?next=' + next_url
        soup = self.soup(self.client.get(url))
        form = soup.select_one('#password-set-form')
        self.assertEqual(form['action'], reverse('account_set_password'))
        self.assertEqual(form.select_one('input[name="next"]')['value'], next_url)
        self.assertIsNotNone(form.select_one('button[name="action"]'))
        response = self.client.post(url, {'password1': 'sample-secret-password', 'password2': 'different-password', 'next': next_url})
        self.assertIsNotNone(self.soup(response).select_one('.admin-form-errors'))
        self.assertNotContains(response, 'sample-secret-password')
        self.assertNotContains(response, 'different-password')
        response = self.client.post(url, {'password1': 'sample-secret-password', 'password2': 'sample-secret-password', 'next': next_url, 'action': ''})
        self.assertRedirects(response, next_url, fetch_redirect_response=False)
        self.personal.refresh_from_db()
        self.assertTrue(self.personal.check_password('sample-secret-password'))

    def test_password_change_works_through_both_views(self):
        next_url = reverse('account:settings')
        for name, fields in [
            ('account:password_change', ('old_password', 'new_password1', 'new_password2')),
            ('account_change_password', ('oldpassword', 'password1', 'password2')),
        ]:
            with self.subTest(page=name):
                self.personal.set_password('testpass123')
                self.personal.save(update_fields=['password'])
                self.client.force_login(self.personal)
                url = reverse(name) + '?next=' + next_url
                soup = self.soup(self.client.get(url))
                self.assertIsNotNone(soup.select_one('#account-edit-form button[type="submit"]'))
                if name == 'account_change_password':
                    self.assertEqual(soup.select_one('input[name="next"]')['value'], next_url)
                response = self.client.post(url, {fields[0]: 'wrong-secret-password', fields[1]: '', fields[2]: ''})
                self.assertIsNotNone(self.soup(response).select_one('.admin-form-errors'))
                self.assertNotContains(response, 'wrong-secret-password')
                response = self.client.post(url, {
                    fields[0]: 'testpass123', fields[1]: 'changed-secret-password',
                    fields[2]: 'changed-secret-password', 'next': next_url,
                })
                self.assertRedirects(response, next_url, fetch_redirect_response=False)
                self.personal.refresh_from_db()
                self.assertTrue(self.personal.check_password('changed-secret-password'))

    def test_reauthentication_preserves_next_errors_alternatives_and_success(self):
        next_url = reverse('account:settings')
        url = reverse('account_reauthenticate') + '?next=' + next_url
        with patch('allauth.account.views.ReauthenticateView.get_reauthentication_alternatives', return_value=[
            {'url': '/alternative/?next=' + next_url, 'description': '別の確認方法'},
        ]):
            response = self.client.get(url)
        soup = self.soup(response)
        form = soup.select_one('#reauthenticate-form')
        self.assertEqual(form['action'], reverse('account_reauthenticate'))
        self.assertEqual(form.select_one('input[name="next"]')['value'], next_url)
        self.assertIsNotNone(soup.select_one('a[href="/alternative/?next=' + next_url + '"]'))
        response = self.client.post(url, {'password': 'wrong-secret-password', 'next': next_url})
        self.assertIsNotNone(self.soup(response).select_one('.admin-form-errors'))
        self.assertNotContains(response, 'wrong-secret-password')
        response = self.client.post(url, {'password': 'testpass123', 'next': next_url})
        self.assertRedirects(response, next_url, fetch_redirect_response=False)

    def test_password_set_and_change_keep_existing_eligibility_redirects(self):
        self.assertRedirects(self.client.get(reverse('account_set_password')), reverse('account_change_password'),
                             fetch_redirect_response=False)
        self.personal.set_unusable_password()
        self.personal.save(update_fields=['password'])
        self.client.force_login(self.personal)
        self.assertRedirects(self.client.get(reverse('account_change_password')), reverse('account_set_password'),
                             fetch_redirect_response=False)
