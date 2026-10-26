"""管理画面の表示変更でフォーム契約と閲覧範囲を変えないことを検証する。"""
from datetime import timedelta
from unittest.mock import patch

from bs4 import BeautifulSoup
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from community.models import CommunityMember
from tests.factories import (
    make_community,
    make_discord_linked_user,
    make_event,
    make_event_detail,
)

from analytics.models import Campaign, PageAnalytics, SearchConsoleQuery


class AnalyticsAdminUITest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_discord_linked_user(user_name='ui-owner', email='ui-owner@example.com')
        cls.staff = make_discord_linked_user(user_name='ui-staff', email='ui-staff@example.com')
        cls.operator = make_discord_linked_user(
            user_name='ui-operator', email='ui-operator@example.com', is_staff=True,
        )
        cls.superuser = make_discord_linked_user(
            user_name='ui-superuser', email='ui-superuser@example.com', is_staff=True, is_superuser=True,
        )
        cls.presenter = make_discord_linked_user(user_name='ui-presenter', email='ui-presenter@example.com')
        cls.community = make_community(name='管理対象の集会', owner=cls.owner)
        cls.other = make_community(name='権限外の集会')
        CommunityMember.objects.create(community=cls.community, user=cls.staff, role=CommunityMember.Role.STAFF)
        cls.campaign = Campaign.objects.create(
            community=cls.community, name='配布するチラシ', utm_source='flyer',
            utm_medium='qr', utm_campaign='admin-ui-flyer',
        )
        cls.other_campaign = Campaign.objects.create(
            community=cls.other, name='別集会のチラシ', utm_source='flyer',
            utm_medium='qr', utm_campaign='other-ui-flyer',
        )
        detail = make_event_detail(make_event(cls.community), applicant=cls.presenter)
        yesterday = timezone.localdate() - timedelta(days=1)
        PageAnalytics.objects.create(
            community=cls.community, date=yesterday, page_path=f'/event/detail/{detail.pk}/',
            content_type=PageAnalytics.ContentType.EVENT_DETAIL, object_id=detail.pk,
            pv=7, users=3, sessions=4,
        )
        SearchConsoleQuery.objects.create(
            community=cls.community, date=yesterday, page_path=f'/event/detail/{detail.pk}/',
            content_type=PageAnalytics.ContentType.EVENT_DETAIL, object_id=detail.pk,
            query='権限内の検索語', clicks=2, impressions=10,
        )

    def get_page(self, name, *, pk=None, **params):
        return self.client.get(reverse(f'analytics:{name}', args=[pk] if pk else None), params)

    def soup(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'confirm(')
        self.assertTemplateUsed(response, 'ta_hub/admin_base.html')
        return BeautifulSoup(response.content, 'html.parser')

    def test_page_headings_and_current_navigation(self):
        self.client.force_login(self.owner)
        cases = (
            ('dashboard', None, 'アクセス解析: 管理対象の集会', 'アクセス'),
            ('search_queries', None, '検索キーワード: 管理対象の集会', '検索キーワード'),
            ('campaign_list', None, '配布リンクの管理: すべての所属集会', '配布リンク'),
            ('campaign_create', None, '配布リンクの作成', '配布リンク'),
            ('campaign_update', self.campaign.pk, '配布リンクの編集: 管理対象の集会', '配布リンク'),
            ('campaign_delete', self.campaign.pk, '配布リンクの削除確認: 管理対象の集会', '配布リンク'),
        )
        for name, pk, heading, active in cases:
            with self.subTest(page=name):
                response = self.get_page(name, pk=pk, community=self.community.pk)
                soup = self.soup(response)
                self.assertEqual([h.get_text(strip=True) for h in soup.select('h1')], [heading])
                self.assertEqual(soup.select_one('.admin-subnav [aria-current="page"]').get_text(strip=True), active)

    def test_role_matrix_preserves_data_and_form_access(self):
        for user, own_access, all_access in (
            (self.owner, True, False), (self.staff, True, False),
            (self.operator, False, False), (self.superuser, True, True),
            (self.presenter, False, False),
        ):
            self.client.force_login(user)
            with self.subTest(user=user.user_name):
                response = self.get_page('campaign_list')
                soup = self.soup(response)
                self.assertEqual(bool(soup.select('.analytics-campaign')), own_access)
                if own_access:
                    self.assertContains(response, self.campaign.name)
                else:
                    self.assertNotContains(response, self.campaign.name)
                self.assertEqual(self.other_campaign.name in response.content.decode(), all_access)
                for page in ('campaign_update', 'campaign_delete'):
                    self.assertEqual(self.get_page(page, pk=self.campaign.pk).status_code, 200 if own_access else 404)
                    self.assertEqual(self.get_page(page, pk=self.other_campaign.pk).status_code, 200 if all_access else 404)
                create = self.soup(self.get_page('campaign_create'))
                options = {o['value'] for o in create.select('[name="community"] option') if o['value']}
                self.assertEqual(options, {str(self.community.pk), str(self.other.pk)} if all_access else {str(self.community.pk)} if own_access else set())
                for page in ('dashboard', 'search_queries'):
                    response = self.get_page(page, community=self.community.pk)
                    self.soup(response)
                    self.assertEqual(response.context['has_access'], own_access)
                    if not own_access:
                        self.assertContains(response, '所属している集会がありません')
                        self.assertNotContains(response, '権限内の検索語')
                    elif page == 'search_queries':
                        self.assertContains(response, '権限内の検索語')

    def test_anonymous_redirects_for_all_pages(self):
        for page, pk in (('dashboard', None), ('search_queries', None), ('campaign_list', None),
                         ('campaign_create', None), ('campaign_update', self.campaign.pk),
                         ('campaign_delete', self.campaign.pk)):
            with self.subTest(page=page):
                self.assertEqual(self.get_page(page, pk=pk).status_code, 302)

    def test_role_matrix_preserves_post_permissions(self):
        Campaign.objects.filter(pk=self.campaign.pk).update(qr_image='qr_codes/dummy.png')
        data = {
            'community': self.community.pk, 'name': '保存した配布リンク', 'utm_source': 'flyer',
            'utm_medium': 'qr', 'utm_campaign': self.campaign.utm_campaign, 'landing_path': '/',
        }
        for user, allowed in ((self.owner, True), (self.staff, True), (self.operator, False),
                              (self.superuser, True), (self.presenter, False)):
            self.client.force_login(user)
            with self.subTest(user=user.user_name):
                response = self.client.post(reverse('analytics:campaign_update', args=[self.campaign.pk]), data)
                self.assertEqual(response.status_code, 302 if allowed else 404)
                if not allowed:
                    response = self.client.post(reverse('analytics:campaign_delete', args=[self.campaign.pk]))
                    self.assertEqual(response.status_code, 404)
                    response = self.client.post(reverse('analytics:campaign_create'), {
                        **data, 'utm_campaign': 'unauthorized-create',
                    })
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('community', response.context['form'].errors)
        self.assertFalse(Campaign.objects.filter(utm_campaign='unauthorized-create').exists())
        self.assertTrue(Campaign.objects.filter(pk=self.campaign.pk).exists())

    def test_inline_and_standalone_delete_share_confirmation_and_target(self):
        self.client.force_login(self.owner)
        url = reverse('analytics:campaign_delete', args=[self.campaign.pk])
        for name, pk, hidden in (('campaign_list', None, True), ('campaign_delete', self.campaign.pk, False)):
            with self.subTest(page=name):
                soup = self.soup(self.get_page(name, pk=pk))
                panel = soup.select_one('.confirm-panel')
                self.assertEqual(panel.has_attr('hidden'), hidden)
                form = panel.find('form')
                self.assertEqual((form['method'], form['action']), ('post', url))
                self.assertIsNotNone(form.select_one('[name="csrfmiddlewaretoken"]'))
                self.assertIn(self.campaign.name, form.get_text())
                self.assertIn(self.community.name, form.get_text())
                self.assertIn(self.campaign.utm_campaign, form.get_text())
                self.assertIn('配布リンクを削除する', form.button.get_text())
                if hidden:
                    trigger = soup.select_one('[data-confirm-panel]')
                    self.assertEqual(trigger['data-confirm-panel'], panel['id'])
                    self.assertIsNotNone(trigger.find_parent(class_='dropdown-menu'))
        response = self.client.post(url)
        self.assertRedirects(response, reverse('analytics:campaign_list'), fetch_redirect_response=False)
        self.assertFalse(Campaign.objects.filter(pk=self.campaign.pk).exists())
        self.assertTrue(Campaign.objects.filter(pk=self.other_campaign.pk).exists())

    def test_form_contract_and_fixed_save_scope(self):
        self.client.force_login(self.owner)
        for page, pk in (('campaign_create', None), ('campaign_update', self.campaign.pk)):
            with self.subTest(page=page):
                soup = self.soup(self.get_page(page, pk=pk))
                form = soup.select_one('form#campaign-form')
                self.assertEqual(form['method'], 'post')
                self.assertFalse(form.has_attr('action'))
                self.assertTrue(form.has_attr('data-admin-dirty'))
                self.assertEqual({f['name'] for f in form.select('[name]')}, {
                    'csrfmiddlewaretoken', 'community', 'name', 'landing_path', 'utm_source',
                    'utm_medium', 'utm_campaign', 'distributed_at', 'note',
                })
                self.assertEqual([h.get_text(strip=True) for h in form.select('h2')], ['対象と配布先', '計測名', '配布予定'])
                save = soup.select_one('[data-admin-action-bar] button[type="submit"]')
                self.assertEqual(save['form'], form['id'])
                self.assertEqual(soup.select_one('[data-admin-form-status]')['data-admin-form-status'], form['id'])
                self.assertFalse(save.has_attr('name'))

    @patch('analytics.campaign_views.generate_qr_png')
    def test_edit_errors_and_success_keep_values_and_redirect(self, generate):
        self.client.force_login(self.owner)
        data = {
            'community': self.community.pk, 'name': '更新した配布リンク', 'utm_source': 'flyer',
            'utm_medium': 'qr', 'utm_campaign': self.campaign.utm_campaign, 'landing_path': '/',
            'distributed_at': '2026-11-01', 'note': '配布予定のメモ',
        }
        url = reverse('analytics:campaign_update', args=[self.campaign.pk])
        response = self.client.post(url, {**data, 'name': '', 'landing_path': 'https://invalid.example/'})
        soup = self.soup(response)
        self.assertIsNotNone(soup.select_one('.admin-form-errors a[href="#id_name"]'))
        self.assertIsNotNone(soup.select_one('.admin-form-errors a[href="#id_landing_path"]'))
        self.assertEqual(soup.select_one('[name="note"]').get_text().lstrip("\n"), data['note'])
        # A saved QR avoids file generation for edits that do not change the URL.
        Campaign.objects.filter(pk=self.campaign.pk).update(qr_image='qr_codes/dummy.png')
        response = self.client.post(url, data)
        self.assertRedirects(response, reverse('analytics:campaign_list'), fetch_redirect_response=False)
        self.campaign.refresh_from_db()
        self.assertEqual((self.campaign.name, self.campaign.note), (data['name'], data['note']))
        generate.assert_not_called()

    def test_empty_list_uses_common_state(self):
        self.client.force_login(self.owner)
        self.campaign.delete()
        soup = self.soup(self.get_page('campaign_list'))
        self.assertIn('まだ配布リンクがありません', soup.select_one('.admin-empty-state').get_text())

    def test_subnav_keeps_period_without_community(self):
        # 複数集会の合算表示（集会未選択）でも期間を引き継ぐ
        CommunityMember.objects.create(community=self.other, user=self.owner, role=CommunityMember.Role.OWNER)
        self.client.force_login(self.owner)
        for page in ('dashboard', 'search_queries'):
            with self.subTest(page=page):
                response = self.get_page(page, days=90)
                self.assertIsNone(response.context['selected_community'])
                soup = self.soup(response)
                links = {a.get_text(strip=True): a['href'] for a in soup.select('.admin-subnav a')}
                self.assertIn('days=90', links['アクセス'])
                self.assertIn('days=90', links['検索キーワード'])

    def test_list_keeps_selectable_url_for_manual_copy(self):
        # クリップボードが使えない時のために、配布用 URL を画面に残す
        self.client.force_login(self.owner)
        soup = self.soup(self.get_page('campaign_list'))
        self.assertEqual(soup.select_one('.analytics-campaign-url code').get_text(), self.campaign.url)
