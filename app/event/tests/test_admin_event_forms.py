"""管理画面の表示と、既存のフォーム・権限の契約を確認する。"""

from unittest.mock import patch

from bs4 import BeautifulSoup
from django.test import TestCase
from django.urls import reverse

from tests.factories import (
    make_community,
    make_community_member,
    make_discord_linked_user,
    make_event,
    make_event_detail,
)


class AdminEventFormsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = {}
        for role in ('owner', 'staff', 'operator', 'superuser', 'applicant'):
            cls.users[role] = make_discord_linked_user(
                user_name=f'form_{role}', email=f'{role}@example.com',
                is_staff=role == 'operator', is_superuser=role == 'superuser',
            )
        cls.secret = 'dummy-webhook-that-must-not-appear'
        cls.community = make_community(
            name='フォーム検証集会', owner=cls.users['owner'],
            webhook_url=cls.secret, recording_allowed=True,
        )
        make_community_member(cls.community, cls.users['staff'])
        cls.event = make_event(cls.community)
        cls.detail = make_event_detail(
            cls.event, applicant=cls.users['applicant'], status='approved',
        )
        cls.pending = make_event_detail(
            cls.event, applicant=cls.users['applicant'], status='pending',
            theme='審査待ちの発表',
        )

    def login(self, role='owner'):
        self.client.force_login(self.users[role])
        session = self.client.session
        session['active_community_id'] = self.community.pk
        session.save()

    def routes(self):
        return [
            ('イベント登録', 'event:calendar_create', {}),
            ('開催日の変更', 'event:date_update', {'pk': self.event.pk}),
            ('開始時刻の変更', 'event:update', {'pk': self.event.pk}),
            ('発表の追加', 'event:detail_create', {'event_pk': self.event.pk}),
            ('発表の編集', 'event:detail_update', {'pk': self.detail.pk}),
            ('発表の削除', 'event:detail_delete', {'pk': self.detail.pk}),
            ('発表申請の審査', 'event:lt_application_review', {'pk': self.pending.pk}),
            ('発表を申請', 'event:lt_application_create', {'community_pk': self.community.pk}),
            ('発表申請完了', 'event:lt_application_complete', {'community_pk': self.community.pk}),
            ('カレンダー設定', 'community:calendar_update', {}),
        ]

    def test_all_screens_use_admin_shell_and_target_heading(self):
        self.login()
        for title, route, kwargs in self.routes():
            with self.subTest(route=route):
                response = self.client.get(reverse(route, kwargs=kwargs))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, 'ta_hub/admin_base.html')
                self.assertContains(response, f'<h1>{title}: {self.community.name}</h1>', html=True)
                self.assertNotContains(response, 'confirm(')
                self.assertNotContains(response, self.secret)
                soup = BeautifulSoup(response.content, 'html.parser')
                self.assertEqual(len(soup.select('h1')), 1)
                self.assertEqual(soup.select_one('.breadcrumb a')['href'], reverse('event:my_list'))

    def test_form_controls_and_upload_attributes_are_preserved(self):
        self.login()
        for _, route, kwargs in self.routes():
            if route in ('event:detail_delete', 'event:lt_application_complete'):
                continue
            with self.subTest(route=route):
                response = self.client.get(reverse(route, kwargs=kwargs))
                soup = BeautifulSoup(response.content, 'html.parser')
                form = response.context['form']
                submitted_names = {node.get('name') for node in soup.select('input, select, textarea, button')}
                for field in form:
                    # 集会名はセッションから決まるため、従来の登録画面でも入力させない。
                    if route == 'event:calendar_create' and field.name == 'community_name':
                        continue
                    self.assertIn(field.html_name, submitted_names)
                if route in ('event:detail_create', 'event:detail_update', 'community:calendar_update'):
                    self.assertIsNotNone(soup.select_one('form[enctype="multipart/form-data"]'))

    def test_save_bars_reference_only_their_original_form(self):
        self.login()
        cases = [
            ('event:calendar_create', {}, 'calendar-form', '登録'),
            ('event:date_update', {'pk': self.event.pk}, 'schedule-form', '変更する'),
            ('event:update', {'pk': self.event.pk}, 'schedule-form', '保存'),
            ('event:lt_application_create', {'community_pk': self.community.pk}, 'application-form', '申請する'),
            ('community:calendar_update', {}, 'calendar-entry-form', '更新'),
        ]
        for route, kwargs, form_id, label in cases:
            with self.subTest(route=route):
                response = self.client.get(reverse(route, kwargs=kwargs))
                soup = BeautifulSoup(response.content, 'html.parser')
                self.assertIsNotNone(soup.select_one(f'form#{form_id}[data-admin-dirty]'))
                bars = soup.select('[data-admin-action-bar]')
                self.assertEqual(len(bars), 1)
                button = bars[0].select_one('button[type="submit"]')
                self.assertEqual(button['form'], form_id)
                self.assertEqual(button.get_text(strip=True), label)

    def test_role_access_remains_unchanged(self):
        # 運営・superuser でも、集会所属が必須の画面の権限は増やさない。
        expected = {
            'owner':     [200, 200, 200, 200, 200, 200, 200, 200, 200, 200],
            'staff':     [200, 200, 200, 200, 200, 200, 200, 200, 200, 200],
            'operator':  [302, 302, 302, 403, 302, 403, 302, 200, 200, 404],
            'superuser': [302, 200, 200, 200, 200, 200, 302, 200, 200, 404],
            'applicant': [302, 302, 302, 403, 200, 403, 302, 200, 200, 404],
        }
        for role, statuses in expected.items():
            self.login(role)
            for (_, route, kwargs), status in zip(self.routes(), statuses):
                with self.subTest(role=role, route=route):
                    response = self.client.get(reverse(route, kwargs=kwargs))
                    self.assertEqual(response.status_code, status)

    def test_applicant_datetime_is_disabled(self):
        self.login('applicant')
        response = self.client.get(reverse('event:detail_update', args=[self.detail.pk]))
        soup = BeautifulSoup(response.content, 'html.parser')
        for name in ('start_time', 'duration'):
            self.assertTrue(soup.select_one(f'[name="{name}"]').has_attr('disabled'))

    @patch('event.forms.event_detail.is_event_detail_datetime_locked', return_value=True)
    def test_datetime_lock_warning_appears_once(self, _locked):
        self.login()
        response = self.client.get(reverse('event:detail_update', args=[self.detail.pk]))
        self.assertContains(response, response.context['form'].datetime_lock_message, count=1)
        self.assertTrue(response.context['form'].fields['start_time'].disabled)
        self.assertTrue(response.context['form'].fields['duration'].disabled)

    def test_review_status_and_read_only_states(self):
        self.login()
        for status, label in [('pending', '承認待ち'), ('approved', '承認済み'), ('rejected', '却下')]:
            with self.subTest(status=status):
                self.pending.status = status
                self.pending.rejection_reason = '日程の都合'
                self.pending.save(update_fields=['status', 'rejection_reason'])
                response = self.client.get(reverse('event:lt_application_review', args=[self.pending.pk]))
                soup = BeautifulSoup(response.content, 'html.parser')
                self.assertEqual(soup.select_one('.detail-chip').get_text(strip=True), label)
                buttons = soup.select('button[form="review-form"][name="action"]')
                self.assertEqual({b['value'] for b in buttons}, {'approve', 'reject'} if status == 'pending' else set())
                self.assertContains(response, '追加情報はありません。')
                if status == 'rejected':
                    self.assertContains(response, '日程の都合')

    def test_review_error_keeps_rejection_reason_visible(self):
        self.login()
        response = self.client.post(reverse('event:lt_application_review', args=[self.pending.pk]), {
            'action': 'reject', 'rejection_reason': '',
        })
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertNotIn('d-none', soup.select_one('#rejection-reason-field').get('class', []))
        self.assertTrue(response.context['form'].errors)

    def test_application_empty_state_has_no_submit_bar(self):
        self.login('applicant')
        self.event.accepts_lt_application = False
        self.event.save(update_fields=['accepts_lt_application'])
        response = self.client.get(reverse('event:lt_application_create', args=[self.community.pk]))
        self.assertContains(response, '申請可能な日程がありません')
        self.assertNotContains(response, 'id="application-form"')
        self.assertNotContains(response, 'data-admin-action-bar')

    def test_invalid_application_keeps_errors_and_consent_choices(self):
        self.login('applicant')
        url = reverse('event:lt_application_create', args=[self.community.pk])
        response = self.client.post(url, {'event': self.event.pk, 'theme': '', 'speaker': ''})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        self.assertContains(response, 'admin-form-errors')
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertTrue(soup.select('[name="article_consent"]'))
        self.assertTrue(soup.select('[name="recording_policy"]'))

    def test_detail_error_keeps_hidden_values_and_generation_feedback(self):
        self.login()
        response = self.client.post(reverse('event:detail_update', args=[self.detail.pk]), {
            'detail_type': 'LT', 'theme': '', 'speaker': '', 'start_time': '22:00', 'duration': 30,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        soup = BeautifulSoup(response.content, 'html.parser')
        for field in response.context['form'].hidden_fields():
            self.assertIsNotNone(soup.select_one(f'input[type="hidden"][name="{field.html_name}"]'))
        self.assertIsNotNone(soup.select_one('#detail-form [data-article-submit-button]'))
        self.assertIsNotNone(soup.select_one('#detail-form [data-article-submit-feedback]'))

    def test_delete_uses_open_confirm_panel_and_original_post_url(self):
        self.login()
        url = reverse('event:detail_delete', args=[self.detail.pk])
        response = self.client.get(url)
        self.assertTemplateUsed(response, 'ta_hub/admin/includes/confirm_panel.html')
        soup = BeautifulSoup(response.content, 'html.parser')
        panel = soup.select_one('.confirm-panel')
        self.assertFalse(panel.has_attr('hidden'))
        self.assertEqual(panel.select_one('form')['action'], url)
        self.assertEqual(panel.select_one('a')['href'], reverse('event:detail', args=[self.detail.pk]))
