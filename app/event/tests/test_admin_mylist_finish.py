"""イベント管理の仕上げ・本人一覧・紐づけ確認の表示契約。"""

from datetime import timedelta

from bs4 import BeautifulSoup
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from event.services.speaker_invite import create_invite_token
from event.views.speaker_link import SPEAKER_INVITE_SESSION_KEY
from tests.factories import make_community, make_community_member, make_event, make_event_detail, make_user
from utils.vrchat_time import get_vrchat_today
from website.constants import SITE_NAME_SEO


@override_settings(DISCORD_AUTH_REQUIRED=False)
class AdminMyListFinishTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = {
            role: make_user(user_name=role, email=f'{role}@example.com', **flags)
            for role, flags in (
                ('owner', {}), ('staff', {}), ('operator', {'is_staff': True}),
                ('superuser', {'is_superuser': True}), ('applicant', {}),
            )
        }
        cls.community = make_community(name='表示テスト集会', owner=cls.users['owner'])
        make_community_member(cls.community, cls.users['staff'])
        cls.event = make_event(cls.community, event_date=get_vrchat_today() + timedelta(days=7))
        cls.details = {
            role: make_event_detail(cls.event, applicant=user, status='approved', theme=f'{role}の発表')
            for role, user in cls.users.items()
        }
        cls.invitable = make_event_detail(cls.event, status='approved', theme='紐づけ対象の発表', applicant=None)

    def setUp(self):
        cache.clear()

    def soup(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'confirm(')
        return BeautifulSoup(response.content, 'html.parser')

    def test_my_list_heading_and_role_visibility_stay_membership_based(self):
        for role, user in self.users.items():
            with self.subTest(role=role):
                self.client.force_login(user)
                response = self.client.get(reverse('event:my_list'))
                soup = self.soup(response)
                self.assertTemplateUsed(response, 'ta_hub/admin_base.html')
                self.assertEqual(len(soup.select('h1')), 1)
                has_membership = role in ('owner', 'staff')
                if has_membership:
                    self.assertEqual(soup.h1.get_text(strip=True), f'イベント管理: {self.community.name}')
                for detail in self.details.values():
                    edit = soup.select_one(f'a[href="{reverse("event:detail_update", args=[detail.pk])}"]')
                    self.assertEqual(edit is not None, has_membership)
                self.assertEqual(
                    soup.select_one('[data-testid="discord-announce-link"]') is not None,
                    role in ('operator', 'superuser'),
                )
                personal_link = soup.select_one('nav[aria-label="個人用"] a')
                self.assertEqual(personal_link['href'], reverse('account:lt_application_list'))
                self.assertEqual(personal_link.get_text(strip=True), '自分の発表申請')
                self.client.logout()

    def test_presentation_rows_have_at_most_three_visible_actions(self):
        self.client.force_login(self.users['owner'])
        for status in ('approved', 'pending', 'rejected'):
            with self.subTest(status=status):
                detail = make_event_detail(
                    self.event, status=status, theme=f'操作数の確認{status}',
                    youtube_url='https://www.youtube.com/watch?v=abcdefghijk',
                )
                soup = self.soup(self.client.get(reverse('event:my_list')))
                row = soup.select_one(f'#presentation-{detail.pk}')
                actions = row.select_one('.presentation-actions')
                # 常設は「…」を除いて 3 個まで（承認待ちは 承認・却下・申請内容）
                self.assertLessEqual(len(actions.select(':scope > .btn')), 3)
                self.assertIsNotNone(actions.select_one(':scope > .dropdown > .btn'))
                menu = actions.select_one('.dropdown-menu')
                button = menu.select_one('.generate-button')
                self.assertEqual(button.find_parent('form')['action'], reverse('event:generate_blog', args=[detail.pk]))
                self.assertEqual(button.find_parent('form')['method'], 'post')
                self.assertIsNotNone(button.find_parent('form').select_one('[name="csrfmiddlewaretoken"]'))
                self.assertIsNotNone(row.select_one('.generation-status[hidden][role="status"]'))
                self.assertIsNotNone(row.select_one(f'#detail-delete-{detail.pk} form'))
                if status == 'pending':
                    review = actions.select_one(f':scope > a[href="{reverse("event:lt_application_review", args=[detail.pk])}"]')
                    self.assertIsNotNone(review)
                    self.assertIsNotNone(actions.select_one('[data-bs-target^="#approveModal"]'))
                    self.assertIsNotNone(actions.select_one('[data-bs-target^="#rejectModal"]'))

    def test_mine_shell_and_edit_links_are_limited_to_own_approved_presentations_for_all_roles(self):
        pending = make_event_detail(self.event, applicant=self.users['applicant'], status='pending', theme='承認待ちの発表')
        for role, user in self.users.items():
            with self.subTest(role=role):
                self.client.force_login(user)
                response = self.client.get(reverse('event:detail_history'), {'mine': '1'})
                soup = self.soup(response)
                self.assertTemplateUsed(response, 'ta_hub/admin_base.html')
                self.assertEqual(soup.h1.get_text(strip=True), f'自分の発表: {user.display_label}')
                self.assertEqual(len(soup.select('h1')), 1)
                self.assertEqual(len(soup.select('title')), 1)
                self.assertTrue(soup.title.get_text().endswith(f' - {SITE_NAME_SEO}'))
                self.assertContains(response, 'history.jpg')
                self.assertIsNotNone(soup.select_one('.mylist-nav a[aria-current="page"]'))
                self.assertEqual(soup.select_one('input[name="mine"]')['value'], '1')
                cards = soup.select('.mine-presentation')
                self.assertEqual(len(cards), 1)
                self.assertIn(self.details[role].theme, cards[0].get_text())
                edit = cards[0].select_one('.presentation-actions a')
                self.assertEqual(edit['href'], reverse('event:detail_update', args=[self.details[role].pk]))
                self.assertNotContains(response, pending.theme)
                self.client.logout()

    def test_public_and_anonymous_mine_pages_do_not_load_admin_assets(self):
        for user in (None, self.users['applicant']):
            if user:
                self.client.force_login(user)
            for params in ({}, {'type': 'special'}, {'mine': 'other'}, *([{'mine': '1'}] if not user else [])):
                with self.subTest(user=user, params=params):
                    response = self.client.get(reverse('event:detail_history'), params)
                    soup = self.soup(response)
                    self.assertTemplateNotUsed(response, 'ta_hub/admin_base.html')
                    self.assertNotContains(response, 'admin-ui.css')
                    self.assertNotContains(response, 'admin-ui.js')
                    self.assertIsNone(soup.select_one('.hub-admin'))
                    self.assertIsNone(soup.select_one('[aria-label="個人用"]'))
                    self.assertContains(response, 'history.jpg')

    def test_mine_empty_and_filtered_empty_have_shared_empty_state(self):
        self.client.force_login(self.users['applicant'])
        self.details['applicant'].delete()
        for params, title in (
            ({'mine': '1'}, '編集できる発表はまだありません。'),
            ({'mine': '1', 'q': '見つからない語'}, '該当する発表はありません。'),
        ):
            with self.subTest(params=params):
                response = self.client.get(reverse('event:detail_history'), params)
                empty = self.soup(response).select_one('.admin-empty-state')
                self.assertIn(title, empty.get_text())
                self.assertIsNotNone(empty.select_one('a'))

    def test_speaker_confirmation_remains_standalone_and_never_renders_token(self):
        token = create_invite_token(self.invitable)
        for role, user in self.users.items():
            with self.subTest(role=role):
                self.client.force_login(user)
                self.client.post(reverse('event:speaker_invite_token_exchange'), {'token': token})
                response = self.client.get(reverse('event:speaker_link_confirm'))
                soup = self.soup(response)
                self.assertTemplateNotUsed(response, 'ta_hub/base.html')
                self.assertEqual(soup.h1.get_text(strip=True), f'発表者アカウントの紐づけ: {self.invitable.theme}')
                self.assertEqual(len(soup.select('h1')), 1)
                self.assertEqual(soup.select_one('meta[name="robots"]')['content'], 'noindex,nofollow')
                self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
                self.assertEqual(response.headers['Referrer-Policy'], 'same-origin')
                self.assertNotContains(response, token)
                self.assertNotContains(response, 'googletagmanager')
                self.assertNotContains(response, 'gtag(')
                self.assertIsNone(soup.select_one('script[src]'))
                form = soup.select_one('#speaker-link-confirm-form')
                self.assertEqual(form['method'], 'post')
                self.assertFalse(form.has_attr('action'))
                self.assertIsNotNone(form.select_one('[name="csrfmiddlewaretoken"]'))
                self.assertIn(user.display_label, soup.get_text())
                self.client.logout()

    def test_speaker_exchange_and_error_states_keep_feedback_and_no_secrets(self):
        self.client.force_login(self.users['applicant'])
        bootstrap = self.client.get(reverse('event:speaker_link_confirm'))
        soup = self.soup(bootstrap)
        self.assertEqual(soup.h1.get_text(strip=True), '発表者アカウントの紐づけ: 招待リンクの確認')
        self.assertEqual(soup.select_one('#speaker-token-exchange-form')['action'], reverse('event:speaker_invite_token_exchange'))
        self.assertIsNotNone(soup.select_one('#speaker-token-exchange-feedback[hidden]'))
        self.assertContains(bootstrap, "window.history.replaceState(null, '', window.location.pathname)")
        invalid_token = 'invalid-dummy-invite-for-display-test'
        session = self.client.session
        session[SPEAKER_INVITE_SESSION_KEY] = invalid_token
        session.save()
        response = self.client.get(reverse('event:speaker_link_confirm'))
        soup = self.soup(response)
        self.assertIsNotNone(soup.select_one('[role="alert"][aria-live="assertive"]'))
        self.assertNotContains(response, invalid_token)
        self.assertIsNone(soup.select_one('#speaker-link-confirm-form'))
