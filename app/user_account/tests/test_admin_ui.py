"""アカウント管理画面の表示・送信契約の回帰テスト。"""
from datetime import timedelta

from bs4 import BeautifulSoup
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from community.models import CommunityMember
from event.models import EventDetail
from tests.factories import (
    make_community,
    make_community_member,
    make_discord_linked_user,
    make_event,
    make_event_detail,
)
from user_account.models import APIKey
from user_account.tests.utils import TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS)
class AccountAdminUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_discord_linked_user(display_name='発表者サンプル')
        cls.community = make_community(name='技術交流会', owner=cls.user, recording_allowed=True)
        cls.event = make_event(cls.community)
        cls.detail = make_event_detail(cls.event, applicant=cls.user, theme='サンプルテーマ')

    def setUp(self):
        self.client.force_login(self.user)

    def soup(self, response):
        self.assertEqual(response.status_code, 200)
        return BeautifulSoup(response.content, 'html.parser')

    def edit_url(self):
        return reverse('account:lt_application_edit', args=[self.detail.pk])

    def test_all_target_pages_use_admin_base_and_contextual_heading(self):
        account = self.user.socialaccount_set.get(provider='discord')
        pages = [
            ('account:settings', [], 'アカウント設定'),
            ('account:user_update', [], 'ユーザー情報の編集'),
            ('account:user_name_change', [], 'ユーザー名の変更'),
            ('account:password_change', [], 'パスワードの変更'),
            ('account:api_key_list', [], 'API管理'),
            ('account:discord_required', [], 'Discord 連携の案内'),
            ('account:lt_application_list', [], '自分の発表申請'),
            ('account:lt_application_edit', [self.detail.pk], '自分の発表の編集'),
            ('socialaccount_connections', [], 'アカウント連携'),
            ('account:social_disconnect', [account.pk], 'Discord 連携の解除'),
        ]
        for name, args, title in pages:
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=args))
                soup = self.soup(response)
                target = self.community.name if args == [self.detail.pk] and name == 'account:lt_application_edit' else self.user.display_label
                self.assertTemplateUsed(response, 'ta_hub/admin_base.html')
                self.assertEqual(len(soup.select('main h1')), 1)
                self.assertEqual(soup.select_one('main h1').get_text(strip=True), f'{title}: {target}')
                self.assertIn(f'{title} - ', soup.title.get_text())
                self.assertIsNotNone(soup.select_one('.hub-admin nav[aria-label="パンくず"]'))
                self.assertNotContains(response, 'confirm(')
                self.assertIsNotNone(soup.select_one('meta[property="og:image"]'))

    def test_waiting_list_is_only_shown_to_superusers(self):
        for is_staff, is_superuser in [(False, False), (True, False), (True, True), (False, True)]:
            with self.subTest(staff=is_staff, superuser=is_superuser):
                self.user.is_staff = is_staff
                self.user.is_superuser = is_superuser
                self.user.save(update_fields=['is_staff', 'is_superuser'])
                soup = self.soup(self.client.get(reverse('account:settings')))
                self.assertEqual(bool(soup.select_one('a[href="/community/waiting_list/"]')), is_superuser)
                queue = reverse('twitter:tweet_queue_list')
                self.assertEqual(bool(soup.select_one(f'a[href="{queue}"]')), is_staff)

    def test_community_owner_and_staff_keep_existing_switch_posts(self):
        other = make_discord_linked_user(user_name='staff-user', email='staff@example.com')
        make_community_member(self.community, other, CommunityMember.Role.STAFF)
        for user in [self.user, other]:
            with self.subTest(user=user.pk):
                self.client.force_login(user)
                soup = self.soup(self.client.get(reverse('account:settings')))
                forms = soup.select('.hub-admin form[action="/community/switch/"]')
                self.assertEqual(len(forms), 2)
                self.assertEqual({form.select_one('input[name="redirect_to"]')['value'] for form in forms},
                                 {reverse('event:my_list'), reverse('community:settings')})
                for form in forms:
                    self.assertEqual(form['method'], 'post')
                    self.assertEqual(form.select_one('input[name="community_id"]')['value'], str(self.community.pk))
                    self.assertIsNotNone(form.select_one('input[name="csrfmiddlewaretoken"]'))

    def test_ended_community_is_readonly_for_staff_and_reopenable_for_owner(self):
        self.community.end_at = timezone.localdate() - timedelta(days=1)
        self.community.save(update_fields=['end_at'])
        staff = make_discord_linked_user(user_name='ended-staff', email='ended-staff@example.com')
        make_community_member(self.community, staff)
        for user, can_reopen in [(self.user, True), (staff, False)]:
            with self.subTest(user=user.pk):
                self.client.force_login(user)
                response = self.client.get(reverse('account:settings'))
                soup = self.soup(response)
                self.assertEqual(bool(soup.select_one(f'a[href="{reverse("community:reopen", args=[self.community.pk])}"]')), can_reopen)
                self.assertFalse(soup.select('.hub-admin form[action="/community/switch/"]'))
                self.assertContains(response, '終了')

    def test_profile_fields_upload_and_email_confirmation_explanation_survive(self):
        response = self.client.get(reverse('account:user_update'))
        soup = self.soup(response)
        form = soup.select_one('#profile-form')
        self.assertEqual(form['method'], 'post')
        self.assertEqual(form['enctype'], 'multipart/form-data')
        for name in response.context['form'].fields:
            self.assertIsNotNone(form.select_one(f'[name="{name}"]'), name)
        self.assertContains(response, '新しいアドレスの確認が完了するまで現在のアドレスを使用します')
        self.assertIsNotNone(soup.select_one('button[type="submit"][form="profile-form"]'))

    def test_saved_keys_are_masked_and_revoked_through_inline_confirmation(self):
        key, raw = APIKey.create_with_raw_key(user=self.user, name='テスト用')
        response = self.client.get(reverse('account:api_key_list'))
        soup = self.soup(response)
        self.assertNotContains(response, raw)
        self.assertNotContains(response, key.key)
        self.assertContains(response, '••••' + key.key[-4:])
        panel = soup.select_one(f'#revoke-key-{key.pk}')
        self.assertTrue(panel.has_attr('hidden'))
        self.assertEqual(panel.select_one('form')['action'], reverse('account:api_key_delete', args=[key.pk]))
        self.assertIsNotNone(panel.select_one('input[name="csrfmiddlewaretoken"]'))
        self.assertNotContains(response, 'confirm(')

    def test_new_key_starts_masked_and_is_only_delivered_once(self):
        response = self.client.post(reverse('account:api_key_create'), {'name': 'テスト用'}, follow=True)
        soup = self.soup(response)
        self.assertEqual(soup.select_one('#new-key-value').get_text(), '••••••••')
        self.assertEqual(soup.select_one('#reveal-new-key')['aria-expanded'], 'false')
        self.assertEqual(soup.select_one('#copy-new-key')['type'], 'button')
        raw = response.context['new_api_key']
        self.assertNotIn(raw, soup.select_one('.hub-admin').get_text())
        self.assertContains(response, 'navigator.clipboard.writeText(rawKey)')
        self.assertNotContains(response, 'localStorage')
        response = self.client.get(reverse('account:api_key_list'))
        self.assertNotContains(response, raw)
        self.assertIsNone(response.context['new_api_key'])

    def test_application_statuses_empty_and_rejected_actions(self):
        for status, label in [('pending', '承認待ち'), ('approved', '承認済み'), ('rejected', '却下')]:
            with self.subTest(status=status):
                self.detail.status = status
                self.detail.rejection_reason = '別の日程をご検討ください'
                self.detail.save(update_fields=['status', 'rejection_reason'])
                response = self.client.get(reverse('account:lt_application_list'))
                soup = self.soup(response)
                card = soup.select_one(f'article[aria-labelledby="application-{self.detail.pk}"]')
                self.assertEqual(card.select_one('.detail-chip').get_text(strip=True), label)
                self.assertIn(self.community.name, card.get_text())
                self.assertIn(self.event.date.strftime('%Y/%m/%d'), card.get_text())
                self.assertEqual(bool(card.select_one(f'a[href="{self.edit_url()}"]')), status != 'rejected')
                self.assertEqual(bool(card.select_one(f'a[href="{reverse("event:detail", args=[self.detail.pk])}"]')), status == 'approved')
                if status == 'rejected':
                    self.assertContains(response, self.detail.rejection_reason)
        self.detail.delete()
        self.assertContains(self.client.get(reverse('account:lt_application_list')), '発表申請はまだありません')

    def test_edit_keeps_all_fields_hidden_values_and_generation_feedback(self):
        for consent in ['', EventDetail.ArticleConsent.OK, EventDetail.ArticleConsent.NG]:
            with self.subTest(consent=consent):
                self.detail.article_consent = consent
                self.detail.save(update_fields=['article_consent'])
                response = self.client.get(self.edit_url())
                soup = self.soup(response)
                form = soup.select_one('#application-form')
                self.assertEqual(form['enctype'], 'multipart/form-data')
                for name in response.context['form'].fields:
                    self.assertIsNotNone(form.select_one(f'[name="{name}"]'), name)
                self.assertEqual(len(form.select('section.admin-card')), 3)
                self.assertIsNotNone(form.select_one('[data-article-submit-button]'))
                self.assertIsNotNone(form.select_one('[data-article-submit-feedback][hidden]'))
                if consent == EventDetail.ArticleConsent.OK:
                    self.assertContains(response, 'article-auto-generation-note')
                if consent in [EventDetail.ArticleConsent.OK, EventDetail.ArticleConsent.NG]:
                    self.assertIsNone(form.select_one('[name="generate_blog_article"]'))

    def test_invalid_edit_exposes_errors_and_preserves_submitted_theme(self):
        response = self.client.post(self.edit_url(), {'theme': '入力したテーマ', 'speaker': ''})
        soup = self.soup(response)
        self.assertIsNotNone(soup.select_one('.admin-form-errors'))
        self.assertEqual(soup.select_one('[name="theme"]')['value'], '入力したテーマ')
        self.assertContains(response, '発表者を入力してください')

    def test_other_applicant_cannot_edit_even_as_staff_or_superuser(self):
        for is_staff, is_superuser in [(False, False), (True, False), (True, True)]:
            other = make_discord_linked_user(user_name=f'other-{is_staff}-{is_superuser}',
                                             email=f'other-{is_staff}-{is_superuser}@example.com',
                                             is_staff=is_staff, is_superuser=is_superuser)
            self.client.force_login(other)
            with self.subTest(staff=is_staff, superuser=is_superuser):
                self.assertEqual(self.client.get(self.edit_url()).status_code, 404)
                self.assertEqual(self.client.post(self.edit_url(), {'theme': '変更'}).status_code, 404)
                self.assertNotContains(self.client.get(reverse('account:lt_application_list')), self.detail.theme)

    def test_disconnect_keeps_password_and_csrf_in_open_confirmation(self):
        account = self.user.socialaccount_set.get(provider='discord')
        url = reverse('account:social_disconnect', args=[account.pk])
        response = self.client.get(url)
        soup = self.soup(response)
        panel = soup.select_one('#disconnect-confirm')
        self.assertFalse(panel.has_attr('hidden'))
        self.assertIsNotNone(panel.select_one('input[type="password"][name="password"]'))
        self.assertIsNotNone(panel.select_one('input[name="csrfmiddlewaretoken"]'))
        self.assertEqual(panel.select_one('form')['action'], url)
        response = self.client.post(url, {'password': 'wrong-password'})
        self.assertContains(response, 'admin-form-errors')
        self.assertNotContains(response, 'wrong-password')
        self.assertTrue(self.user.socialaccount_set.filter(pk=account.pk).exists())
