"""管理画面の表示契約。権限・送信先・秘密値・エラーの回帰を検証する。"""
from datetime import timedelta

from bs4 import BeautifulSoup
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from community.models import CommunityInvitation, CommunityMember
from tests.factories import make_community, make_community_member, make_user


@override_settings(SOCIALACCOUNT_PROVIDERS={'discord': {'APPS': [{
    'client_id': 'test', 'secret': 'test', 'key': '',
}]}})
class CommunityAdminUITest(TestCase):
    def setUp(self):
        self.owner = make_user(email="ui-owner@example.com", user_name="主催者")
        self.staff = make_user(email="ui-staff@example.com", user_name="スタッフ")
        self.operator = make_user(email="ui-operator@example.com", is_staff=True)
        self.superuser = make_user(email="ui-superuser@example.com", is_superuser=True, is_staff=True)
        self.personal = make_user(email="ui-personal@example.com")
        self.community = make_community(name='画面確認の集会', owner=self.owner, status='approved')
        make_community_member(self.community, self.staff, role=CommunityMember.Role.STAFF)
        self.client.force_login(self.owner)
        session = self.client.session
        session['active_community_id'] = self.community.pk
        session.save()

    def page(self, route, *args, **query):
        return self.client.get(reverse(f'community:{route}', args=args), query)

    def assert_page(self, response, title):
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertEqual(soup.h1.get_text(strip=True), title)
        self.assertIsNotNone(soup.select_one('.hub-admin'))
        self.assertNotContains(response, 'confirm(')
        return soup

    def test_settings_and_update_share_forms_and_save_targets(self):
        for route, title in [('settings', '集会設定'), ('update', '集会情報の編集')]:
            with self.subTest(route=route):
                response = self.page(route)
                soup = self.assert_page(response, f'{title}: {self.community.name}')
                for form_id, target in [
                    ('community-form', reverse('community:update')),
                    ('notification-form', reverse('community:update_webhook', args=[self.community.pk])),
                    ('lt-form', reverse('community:update_lt_settings', args=[self.community.pk])),
                ]:
                    form = soup.find('form', id=form_id)
                    self.assertEqual(form['action'], target)
                    self.assertEqual(form['method'], 'post')
                    self.assertIsNotNone(soup.select_one(f'button[form="{form_id}"]'))
                self.assertEqual(soup.find('form', id='community-form')['enctype'], 'multipart/form-data')
                self.assertEqual(len(soup.select('[data-admin-action-bar]')), 1)
                self.assertIsNotNone(soup.select_one('[name="default_recording_policy_chosen"][value=""]'))

    def test_saved_webhook_is_masked_and_test_send_stays_separate(self):
        secret = 'https://discord.com/api/webhooks/123/dummy-secret-9876'
        self.community.notification_webhook_url = secret
        self.community.save(update_fields=['notification_webhook_url'])
        for route in ('settings', 'update'):
            response = self.page(route)
            self.assertNotContains(response, secret)
            self.assertContains(response, '••••••••9876')
            soup = BeautifulSoup(response.content, 'html.parser')
            field = soup.select_one('#notification-form [name="notification_webhook_url"]')
            self.assertIn('disabled', field.attrs)
            self.assertNotIn('value', field.attrs)
            self.assertEqual(field['type'], 'password')
            self.assertIn('disabled', soup.select_one('button[form="notification-form"]').attrs)
            self.assertIsNotNone(soup.select_one(f'form[action="{reverse("community:test_webhook", args=[self.community.pk])}"]'))
            self.assertIsNotNone(soup.select_one('#clear-webhook [name="notification_webhook_url"][value=""]'))

    def test_pending_keeps_editing_and_shows_locked_previews(self):
        self.community.status = 'pending'
        self.community.save(update_fields=['status'])
        for route in ('settings', 'update'):
            response = self.page(route)
            self.assertContains(response, 'まだ公開されていません')
            self.assertContains(response, 'aria-current="step"')
            soup = BeautifulSoup(response.content, 'html.parser')
            self.assertIsNotNone(soup.select_one('#community-form [name="name"]'))
            self.assertIsNotNone(soup.select_one('#notification-form'))
            self.assertIsNotNone(soup.select_one('#lt-form'))

    def test_pending_owner_keeps_webhook_and_transfer_operations(self):
        self.community.status = 'pending'
        self.community.notification_webhook_url = 'https://discord.com/api/webhooks/1/test'
        self.community.save(update_fields=['status', 'notification_webhook_url'])
        soup = BeautifulSoup(self.page('settings').content, 'html.parser')
        pk = self.community.pk
        self.assertIsNotNone(soup.select_one('#notification-form'))
        self.assertIsNotNone(soup.select_one('button[form="notification-form"]'))
        self.assertIsNotNone(soup.select_one(f'form[action="{reverse("community:test_webhook", args=[pk])}"]'))
        self.assertIsNotNone(soup.select_one('#clear-webhook [name="notification_webhook_url"][value=""]'))
        self.assertIsNotNone(soup.select_one(f'form[action="{reverse("community:create_ownership_transfer", args=[pk])}"]'))
        invitation = CommunityInvitation.objects.create(
            community=self.community, created_by=self.owner,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
            expires_at=timezone.now() + timedelta(days=7),
        )
        soup = BeautifulSoup(self.page('settings').content, 'html.parser')
        revoke = reverse('community:revoke_ownership_transfer', args=[pk, invitation.pk])
        self.assertIsNotNone(soup.select_one(f'form[action="{revoke}"]'))

    def test_settings_guards_unsaved_other_forms_in_action_bar(self):
        soup = BeautifulSoup(self.page('settings').content, 'html.parser')
        notice = soup.select_one('[data-unsaved-other-form-notice]')
        self.assertIsNotNone(notice)
        self.assertIn('hidden', notice.attrs)
        self.assertEqual(notice['role'], 'alert')
        for form_id in ('community-form', 'notification-form', 'lt-form'):
            self.assertIsNotNone(soup.select_one(f'form#{form_id}[data-admin-dirty]'))
            self.assertIsNotNone(soup.select_one(f'[data-admin-form-status="{form_id}"][data-admin-scope]'))
        script = ''.join(tag.get_text() for tag in soup.find_all('script'))
        self.assertIn('adminDirtyState', script)
        self.assertIn('の変更は、この保存で消えます。先にそちらを保存するか、消してよければもう一度押してください。', script)
        self.assertIn('armedButton === button', script)
        self.assertIn("addEventListener('beforeunload'", script)
        self.assertIn("addEventListener('pageshow'", script)
        self.assertNotIn('alert(', script)

    def test_blank_webhook_submit_is_blocked_with_inline_message(self):
        soup = BeautifulSoup(self.page('settings').content, 'html.parser')
        error = soup.select_one('#notification-form [data-webhook-blank-error]')
        self.assertIsNotNone(error)
        self.assertIn('hidden', error.attrs)
        # d-block（display:block !important）は hidden 属性に勝ち、常に表示されてしまう
        self.assertNotIn('d-block', error.get('class', []))
        self.assertIn('Webhook URL を入力してください。通知先を消す時は『通知先を削除…』から操作してください。',
                      error.get_text())
        script = ''.join(tag.get_text() for tag in soup.find_all('script'))
        self.assertIn(".value.trim() === ''", script)

    def test_settings_role_visibility_and_access_are_unchanged(self):
        for user, member in [(self.owner, True), (self.staff, True), (self.operator, False),
                             (self.superuser, False), (self.personal, False)]:
            with self.subTest(user=user.pk):
                self.client.force_login(user)
                response = self.page('settings')
                if member:
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, 'メンバーを管理', count=1 if user == self.owner else 0)
                    self.assertContains(response, '危険な操作', count=2 if user == self.owner else 0)
                else:
                    self.assertRedirects(response, reverse('account:settings'), fetch_redirect_response=False)
                    self.assertEqual(self.page('update').status_code, 403)

    def test_member_page_masks_tokens_and_confirms_named_role(self):
        invitation = CommunityInvitation.create_invitation(self.community, self.owner)
        response = self.page('member_manage', self.community.pk)
        soup = self.assert_page(response, f'メンバー管理: {self.community.name}')
        self.assertNotContains(response, invitation.token)
        self.assertIsNotNone(soup.select_one(f'#remove-member-{self.community.members.get(user=self.staff).pk}'))
        self.assertContains(response, f'{self.staff.display_label}（スタッフ）')
        self.assertContains(response, '招待を取り消す')
        response = self.page('member_manage', self.community.pk, reveal_invitation=invitation.pk)
        self.assertContains(response, reverse('community:accept_invitation', args=[invitation.token]))
        self.assertContains(response, 'リンクをコピー')
        # GET の表示切替では招待や役割は変わらない。
        self.assertTrue(CommunityInvitation.objects.filter(pk=invitation.pk).exists())
        self.assertEqual(self.community.members.count(), 2)

    def test_transfer_link_is_masked_until_explicit_display(self):
        invitation = CommunityInvitation.objects.create(
            community=self.community, created_by=self.owner,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
        )
        self.assertNotContains(self.page('settings'), invitation.token)
        self.assertContains(self.page('settings', reveal_invitation=invitation.pk), invitation.token)
        self.client.force_login(self.staff)
        self.assertNotContains(self.page('settings', reveal_invitation=invitation.pk), invitation.token)

    def test_member_page_empty_and_owner_only_access(self):
        response = self.page('member_manage', self.community.pk)
        self.assertContains(response, '有効な招待リンクはありません')
        for user in (self.staff, self.operator, self.superuser, self.personal):
            self.client.force_login(user)
            self.assertEqual(self.page('member_manage', self.community.pk).status_code, 403)

    def test_waiting_list_is_superuser_only_and_has_empty_state(self):
        for user in (self.owner, self.staff, self.operator, self.personal):
            self.client.force_login(user)
            self.assertEqual(self.page('waiting_list').status_code, 403)
        self.client.force_login(self.superuser)
        self.assertContains(self.page('waiting_list'), '承認待ちはありません')
        self.community.status = 'pending'
        self.community.save(update_fields=['status'])
        response = self.page('waiting_list')
        soup = self.assert_page(response, '承認待ちの集会')
        self.assertIsNotNone(soup.select_one(f'a[href="{reverse("community:detail", args=[self.community.pk])}"]'))
        self.assertNotContains(response, 'まだ公開されていません')

    def test_create_has_progress_existing_fields_and_error_summary(self):
        response = self.page('create')
        soup = self.assert_page(response, '集会の登録を申請する')
        self.assertIsNotNone(soup.select_one('[aria-current="step"]'))
        self.assertIsNotNone(soup.select_one('[name="guidelines_agreed"]'))
        self.assertIsNotNone(soup.select_one('#community-form [name="accepts_lt_application"]'))
        self.assertEqual(soup.select_one('#lt-settings-heading').get_text(strip=True), '発表の受付')
        self.assertIsNotNone(soup.select_one('#create-recording-policy[disabled]'))
        self.assertIsNotNone(soup.select_one('.setup-placeholder #after-approval-heading'))
        self.assertIsNone(soup.select_one('[name="lt_application_template"]'))
        response = self.client.post(reverse('community:create'), {})
        self.assertContains(response, 'admin-form-errors')
        self.assertContains(response, 'href="#id_name"')

    def test_update_error_preserves_bound_form_and_does_not_expose_secrets(self):
        self.community.notification_webhook_url = 'https://discord.com/api/webhooks/123/dummy-test-secret'
        self.community.save(update_fields=['notification_webhook_url'])
        response = self.client.post(reverse('community:update'), {'name': '', 'organizers': '入力途中'})
        self.assertContains(response, 'admin-form-errors')
        self.assertContains(response, 'value="入力途中"')
        self.assertNotContains(response, self.community.notification_webhook_url)

    def test_reopen_warning_form_and_done_keep_existing_navigation(self):
        self.community.end_at = timezone.localdate() - timedelta(days=1)
        self.community.save(update_fields=['end_at'])
        response = self.page('reopen', self.community.pk)
        soup = self.assert_page(response, f'集会の再開: {self.community.name}')
        self.assertContains(response, '以前の開催周期・曜日は未設定に戻ります')
        self.assertContains(response, '自動復元されません')
        self.assertIsNone(soup.select_one('[name="weekdays"]'))
        self.assertIsNone(soup.select_one('[name="frequency"]'))
        self.assertEqual(soup.select_one('button[form="community-form"]').get_text(strip=True), 'この内容で再開する')
        self.community.end_at = None
        self.community.save(update_fields=['end_at'])
        response = self.page('reopen_complete', self.community.pk)
        soup = self.assert_page(response, f'集会の再開完了: {self.community.name}')
        self.assertIsNotNone(soup.select_one('[name="redirect_to"][value="' + reverse('event:calendar_create') + '"]'))

    def test_invitation_and_transfer_acceptance_states(self):
        # スタッフ招待の受諾は青、権限が動く主催者の引き継ぎは赤のまま
        for invitation_type, route, title, submit_class in [
            (CommunityInvitation.InvitationType.STAFF, 'accept_invitation', 'スタッフ招待の受諾', 'btn-primary'),
            (CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER, 'accept_ownership_transfer', '主催者引継ぎの受諾',
             'btn-danger'),
        ]:
            invitation = CommunityInvitation.objects.create(
                community=self.community, created_by=self.owner, invitation_type=invitation_type,
            )
            self.client.force_login(self.personal)
            soup = self.assert_page(self.page(route, invitation.token), f'{title}: {self.community.name}')
            self.assertIsNotNone(soup.select_one('.confirm-panel:not([hidden]) form[method="post"][action=""]'))
            self.assertIsNotNone(soup.select_one(f'.confirm-panel button[type=submit].{submit_class}'))
            self.client.force_login(self.owner)
            response = self.page(route, invitation.token)
            self.assertContains(response, 'あなたは既にこの集会の')
            self.client.logout()
            response = self.page(route, invitation.token)
            self.assertContains(response, 'ログインが必要です')
