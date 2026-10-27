"""運営UIの見出し・個別保存・確認・権限と共有日程表の契約。"""
from datetime import time

from bs4 import BeautifulSoup
from django.urls import reverse

from community.models import CommunityMember
from tests.factories import make_event_detail, make_user
from vket.models import VketCollaboration, VketNotice, VketNoticeReceipt, VketParticipation, VketPresentation
from vket.tests._vket_test_bases import VketManageViewsBase


class ManageAdminUiTests(VketManageViewsBase):
    pages = (
        ('manage', '運営管理'), ('manage_schedule', '日程表'),
        ('manage_notice_list', 'お知らせ管理'),
    )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.superuser)
        self.presentation = VketPresentation.objects.create(
            participation=self.participation1, speaker='ダミー登壇者', theme='ダミー発表',
            requested_start_time=time(21), status=VketPresentation.Status.CONFIRMED,
        )

    def url(self, name, **kwargs):
        return reverse(f'vket:{name}', kwargs={'pk': self.collaboration.pk, **kwargs})

    def page(self, name):
        response = self.client.get(self.url(name))
        self.assertEqual(response.status_code, 200)
        return response, BeautifulSoup(response.content, 'html.parser')

    def test_headings_navigation_and_metadata(self):
        for name, title in self.pages:
            with self.subTest(page=name):
                response, soup = self.page(name)
                self.assertTemplateUsed(response, 'ta_hub/admin_base.html')
                self.assertEqual(len(soup.select('main h1')), 1)
                self.assertEqual(soup.select_one('main h1').get_text(strip=True), f'{title}: {self.collaboration.name}')
                self.assertIn(' - ', soup.title.get_text())
                current = soup.select('.admin-subnav a[aria-current="page"]')
                self.assertEqual(len(current), 1)
                self.assertEqual(current[0]['href'], self.url(name))
                self.assertEqual(soup.select_one('.breadcrumb-item.active').get_text(strip=True), title)
                self.assertNotContains(response, 'confirm(')
                self.assertIsNone(soup.select_one('[data-admin-action-bar]'))
                self.assertContains(response, 'ta_hub/js/admin-ui.js')

    def test_row_forms_keep_post_fields_and_separate_delete(self):
        _, soup = self.page('manage')
        for participation in (self.participation1, self.participation2):
            panel_id = f'participation-{participation.pk}'
            panel = soup.select_one(f'#{panel_id}.confirm-panel[hidden]')
            self.assertIsNotNone(panel)
            self.assertIsNotNone(soup.select_one(f'button[data-confirm-panel="{panel_id}"]'))
            self.assertIsNone(panel.find_parent('form'))
            form = panel.select_one('form')
            self.assertEqual(form['method'], 'post')
            self.assertEqual(form['action'], self.url('manage_participation_update', participation_id=participation.pk))
            self.assertEqual(
                {field['name'] for field in form.select('[name]')},
                {'csrfmiddlewaretoken', 'lifecycle', 'confirmed_date', 'confirmed_start_time',
                 'confirmed_duration', 'admin_note', 'confirm_withdraw'}
                | ({f'pres_{self.presentation.pk}_start_time'} if participation == self.participation1 else set()),
            )
            self.assertEqual(form.select_one('button[type="submit"]').get_text(strip=True), '参加状態・日程を保存')
        delete = soup.select_one(f'#delete-pres-{self.presentation.pk}.confirm-panel[hidden] form')
        self.assertEqual(delete['action'], self.url('manage_presentation_delete', presentation_id=self.presentation.pk))
        self.assertEqual({field['name'] for field in delete.select('[name]')}, {'csrfmiddlewaretoken'})
        self.assertIsNone(delete.find_parent('form'))
        trigger = soup.select_one(f'[data-confirm-panel="delete-pres-{self.presentation.pk}"]')
        self.assertIsNotNone(trigger.find_parent(class_='dropdown-menu'))
        self.assertIn('不参加', soup.select_one(f'#participation-{self.participation1.pk}').get_text())

    def test_withdrawal_confirmation_is_only_shown_for_active_participations(self):
        for lifecycle in (VketParticipation.Lifecycle.DECLINED, VketParticipation.Lifecycle.WITHDRAWN):
            with self.subTest(lifecycle=lifecycle):
                self.participation2.lifecycle = lifecycle
                self.participation2.save(update_fields=['lifecycle'])
                _, soup = self.page('manage')
                checkbox = soup.select_one(
                    f'#participation-{self.participation1.pk} input[name="confirm_withdraw"]'
                )
                self.assertEqual(checkbox['type'], 'checkbox')
                self.assertEqual(checkbox['value'], '1')
                self.assertFalse(checkbox.has_attr('checked'))
                self.assertFalse(checkbox.has_attr('required'))
                label = soup.find('label', attrs={'for': checkbox['id']})
                self.assertEqual(
                    label.get_text(strip=True),
                    '公開済みの発表を削除し、公開イベントとの連携を外すことを確認しました',
                )
                self.assertIsNone(soup.select_one(
                    f'#participation-{self.participation2.pk} input[name="confirm_withdraw"]'
                ))
                self.assertEqual(len(soup.select('input[name="confirm_withdraw"]')), 1)

    def test_presentation_delete_confirmation_identifies_and_escapes_speaker(self):
        cases = (
            ('ダミー登壇者', '', 'ダミー登壇者'),
            ('', '', '発表者未入力'),
            ('  ', 'ダミーテーマ', '発表者未入力「ダミーテーマ」'),
            ('ダミー登壇者', 'ダミーテーマ', 'ダミー登壇者「ダミーテーマ」'),
            ('<script>登壇者</script> & "名前"', '', '<script>登壇者</script> & "名前"'),
        )
        for speaker, theme, name in cases:
            with self.subTest(speaker=speaker, theme=theme):
                self.presentation.speaker = speaker
                self.presentation.theme = theme
                self.presentation.save(update_fields=['speaker', 'theme'])
                _, soup = self.page('manage')
                panel = soup.select_one(f'#delete-pres-{self.presentation.pk}')
                self.assertEqual(panel['aria-label'], f'{name} の発表の削除確認')
                self.assertEqual(
                    panel.select_one('p').get_text(),
                    f'{name} の発表と、紐づく公開済みの発表を削除します。',
                )
                self.assertIsNone(panel.select_one('script'))

    def test_publication_is_separate_and_only_in_locked_phase(self):
        _, soup = self.page('manage')
        self.assertIsNone(soup.select_one('#publication'))
        self.collaboration.phase = VketCollaboration.Phase.LOCKED
        self.collaboration.save(update_fields=['phase'])
        _, soup = self.page('manage')
        form = soup.select_one('#publication #publish-confirm[hidden] form')
        self.assertEqual(form['action'], self.url('manage_publish'))
        # 公開同期は保留中の発表の削除を伴うことがあるため、確認は赤のまま
        self.assertIn('btn-danger', form.select_one('button[type=submit]')['class'])
        self.assertIsNone(soup.select_one('#publish-confirm.confirm-panel--primary'))
        self.assertEqual({field['name'] for field in form.select('[name]')}, {'csrfmiddlewaretoken'})
        self.assertIsNone(form.find_parent('form'))

    def test_schedule_settings_are_card_scoped(self):
        _, soup = self.page('manage_schedule')
        form = soup.select_one('#schedule-settings-form[data-admin-dirty]')
        self.assertEqual(form['action'], self.url('manage_schedule_settings'))
        self.assertEqual({field['name'] for field in form.select('[name]')}, {'csrfmiddlewaretoken', 'schedule_buffer_minutes'})
        self.assertEqual(form.select_one('button[type="submit"]').get_text(strip=True), 'この設定を保存')
        self.assertIsNotNone(soup.select_one('[data-admin-form-status="schedule-settings-form"]'))
        self.assertIn('このカードだけ保存: 入れ替えの間隔', soup.get_text())
        self.assertIsNotNone(soup.select_one('.vket-schedule [role="region"][tabindex="0"]'))

    def test_attention_is_above_list_and_not_repeated_in_table(self):
        self.event1.start_time = time(20)
        self.event1.save(update_fields=['start_time'])
        response, soup = self.page('manage')
        attention = soup.select_one('.action-required')
        self.assertIn('要対応 1', attention.get_text())
        self.assertEqual(len(soup.select('[data-testid="publication-drift-warning"]')), 1)
        self.assertIn('公開イベントの日程', attention.get_text())
        self.assertEqual(response.context['management_attention_count'], 1)
        VketPresentation.objects.create(participation=self.participation2, requested_start_time=time(21))
        response, soup = self.page('manage_schedule')
        self.assertTrue(response.context['overlap_warnings'])
        self.assertIn('重複（発表時間）', soup.select_one('.action-required').get_text())
        self.assertNotIn('重複（発表時間）', soup.select_one('.vket-schedule').get_text())

    def test_notice_modals_keep_actions_fields_and_inline_controls(self):
        notice = VketNotice.objects.create(collaboration=self.collaboration, title='ダミーのお知らせ', body='本文')
        _, soup = self.page('manage_notice_list')
        self.assertIsNotNone(soup.select_one('[data-bs-target="#noticeCreateModal"]'))
        self.assertIsNotNone(soup.select_one(f'[data-edit-url="{self.url("manage_notice_update", notice_id=notice.pk)}"]'))
        for form_id, names in (
            ('noticeCreateForm', {'csrfmiddlewaretoken', 'title', 'body', 'target_scope', 'requires_ack'}),
            ('noticeEditForm', {'csrfmiddlewaretoken', 'title', 'body'}),
        ):
            form = soup.select_one(f'form#{form_id}[data-admin-dirty].js-submit-once')
            self.assertEqual({field['name'] for field in form.select('[name]')}, names)
            self.assertIsNotNone(form.select_one(f'.admin-action-bar:not(.stickybar) button[type="submit"][form="{form_id}"]'))
            self.assertIsNotNone(form.select_one('[data-bs-dismiss="modal"]'))
        self.assertEqual(soup.select_one('#noticeCreateForm')['action'], self.url('manage_notice_create'))

    def test_empty_states(self):
        self.collaboration.participations.all().delete()
        for name, text in (
            ('manage', '参加集会はまだありません'),
            ('manage_schedule', '日程がまだ登録されていません。'),
            ('manage_notice_list', 'お知らせはまだありません。'),
        ):
            with self.subTest(page=name):
                _, soup = self.page(name)
                self.assertIn(text, soup.select_one('.admin-empty-state').get_text())

    def test_saved_secrets_and_receipt_token_are_not_rendered(self):
        notice = VketNotice.objects.create(collaboration=self.collaboration, title='ダミー', body='本文', requires_ack=True)
        receipt = VketNoticeReceipt.objects.create(notice=notice, participation=self.participation1)
        self.collaboration.settings_json = {'webhook_url': 'https://example.invalid/dummy-webhook-secret'}
        self.collaboration.save(update_fields=['settings_json'])
        for name, _ in self.pages:
            with self.subTest(page=name):
                response, _ = self.page(name)
                self.assertNotContains(response, str(receipt.ack_token))
                self.assertNotContains(response, 'dummy-webhook-secret')

    def test_permissions_for_owner_community_staff_operator_superuser_and_presenter(self):
        owner = make_user(user_name='ui_owner', email='ui_owner@example.com')
        staff = make_user(user_name='ui_staff', email='ui_staff@example.com')
        operator = make_user(user_name='ui_operator', email='ui_operator@example.com', is_staff=True)
        presenter = self.normal_user
        for user, role in ((owner, CommunityMember.Role.OWNER), (staff, CommunityMember.Role.STAFF)):
            CommunityMember.objects.create(community=self.community1, user=user, role=role)
        detail = make_event_detail(self.event1, applicant=presenter, status='approved')
        self.presentation.published_event_detail = detail
        self.presentation.save(update_fields=['published_event_detail'])
        for user, allowed in ((owner, False), (staff, False), (operator, True), (self.superuser, True), (presenter, False)):
            self.client.force_login(user)
            for name, _ in self.pages:
                with self.subTest(user=user.user_name, page=name):
                    response = self.client.get(self.url(name))
                    self.assertEqual(response.status_code, 200 if allowed else 403)
            if not allowed:
                for name, kwargs in (
                    ('manage_participation_update', {'participation_id': self.participation1.pk}),
                    ('manage_presentation_delete', {'presentation_id': self.presentation.pk}),
                    ('manage_publish', {}), ('manage_schedule_settings', {}), ('manage_notice_create', {}),
                ):
                    with self.subTest(user=user.user_name, action=name):
                        self.assertEqual(self.client.post(self.url(name, **kwargs)).status_code, 403)
