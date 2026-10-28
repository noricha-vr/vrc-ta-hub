"""参加者向け管理UIの見出し・送信契約・権限と例外画面を検証する。"""
from datetime import timedelta

from bs4 import BeautifulSoup
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from community.models import CommunityMember
from tests.factories import make_user
from vket.models import VketCollaboration, VketNotice, VketNoticeReceipt, VketParticipation, VketPresentation
from vket.tests._vket_test_bases import VketApplyFlowBase


class MemberAdminUiTests(VketApplyFlowBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.client.force_login(self.owner)
        self._set_active_community()
        self.collaboration.settings_json = {'stage_registration_open': True}
        self.collaboration.save(update_fields=['settings_json'])
        self.participation = VketParticipation.objects.create(
            collaboration=self.collaboration, community=self.community,
            progress=VketParticipation.Progress.APPLIED,
            requested_date=timezone.localdate(), requested_start_time='22:00', requested_duration=60,
        )
        self.presentation = VketPresentation.objects.create(
            participation=self.participation, speaker='登壇者', theme='テストの発表',
        )
        notice = VketNotice.objects.create(
            collaboration=self.collaboration, title='確認が必要なお知らせ', body='参加者向けの本文',
            requires_ack=True,
        )
        self.receipt = VketNoticeReceipt.objects.create(notice=notice, participation=self.participation)

    def url(self, name):
        kwargs = {'pk': self.collaboration.pk}
        if name == 'stage_copy':
            kwargs['participation_id'] = self.participation.pk
        return reverse(f'vket:{name}', kwargs=kwargs)

    def page(self, name):
        response = self.client.get(self.url(name))
        self.assertEqual(response.status_code, 200)
        return response, BeautifulSoup(response.content, 'html.parser')

    def test_member_pages_use_shared_heading_and_keep_metadata(self):
        for name, title in (
            ('apply', 'Vket 参加登録'), ('status', 'Vket 参加状況'),
            ('stage_copy', 'ステージ登録用コピー'), ('notice_list', 'Vket お知らせ'),
        ):
            with self.subTest(page=name):
                response, soup = self.page(name)
                self.assertTemplateUsed(response, 'ta_hub/admin_base.html')
                self.assertEqual(soup.select_one('main h1').get_text(strip=True), f'{title}: {self.community.name}')
                self.assertEqual(len(soup.select('main h1')), 1)
                self.assertIsNotNone(soup.select_one('.admin-subnav a[aria-current="page"]'))
                self.assertNotContains(response, 'confirm(')
                self.assertIn(' - ', soup.title.get_text())
                if name != 'stage_copy':
                    self.assertIsNotNone(soup.select_one('meta[property="og:image"]'))
                    self.assertIsNotNone(soup.select_one('meta[name="twitter:image"]'))

    def test_apply_save_bar_submits_only_original_form(self):
        response, soup = self.page('apply')
        form = soup.select_one('form#vket-apply-form[data-admin-dirty]')
        self.assertEqual(form['method'], 'post')
        self.assertIsNone(form.get('action'))
        self.assertIsNotNone(form.select_one('[name="csrfmiddlewaretoken"]'))
        self.assertIsNotNone(form.select_one('[name="lt-TOTAL_FORMS"]'))
        self.assertEqual(len(soup.select('[data-admin-action-bar]')), 1)
        button = soup.select_one('button[form="vket-apply-form"]')
        self.assertEqual(button.get_text(strip=True), '保存する')
        self.assertNotIn('disabled', button.attrs)
        for target in ('schedule', 'presentations', 'organizer-note'):
            self.assertIsNotNone(soup.select_one(f'.admin-toc a[href="#{target}"]'))
            self.assertIsNotNone(form.select_one(f'#{target}.admin-card'))
        self.assertContains(response, "deleteInput.dispatchEvent(new Event('change', {bubbles: true}))")
        self.assertContains(response, "totalForms.dispatchEvent(new Event('change', {bubbles: true}))")

    def test_apply_errors_keep_values_and_field_links(self):
        data = {
            'requested_date': timezone.localdate().isoformat(), 'requested_start_time': 'invalid',
            'requested_duration': '60', 'lt_slot_minutes': '30', 'organizer_note': '残す入力',
            **self._make_formset_data([{'speaker': '入力中の登壇者', 'theme': '入力中の発表', 'lt_start_time': 'invalid'}], initial_forms=1),
        }
        response = self.client.post(self.url('apply'), data)
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertIsNotNone(soup.select_one('.admin-form-errors a[href="#id_requested_start_time"]'))
        self.assertIsNotNone(soup.select_one('.admin-form-errors a[href="#id_lt-0-lt_start_time"]'))
        self.assertContains(response, '残す入力')
        self.assertContains(response, '入力中の登壇者')

    def test_status_attention_and_confirmation_post_contracts(self):
        _, soup = self.page('status')
        attention = soup.select_one('#member-attention')
        self.assertIn('要対応 2', attention.get_text())
        self.assertIsNotNone(attention.select_one(f'a[href="{self.url("notice_list")}?open={self.receipt.notice_id}"]'))
        for panel_id, url in (
            ('stage-register-confirm', reverse('vket:stage_register', kwargs={'pk': self.collaboration.pk})),
            (f'presentation-delete-{self.presentation.pk}', reverse('vket:presentation_delete', kwargs={'pk': self.collaboration.pk, 'presentation_id': self.presentation.pk})),
        ):
            panel = soup.select_one(f'#{panel_id}.confirm-panel[hidden]')
            self.assertIsNotNone(panel)
            self.assertIsNotNone(panel.select_one(f'form[method="post"][action="{url}"]'))
            self.assertIsNotNone(panel.select_one('[name="csrfmiddlewaretoken"]'))
            self.assertIsNotNone(soup.select_one(f'button[data-confirm-panel="{panel_id}"]'))
            self.assertIsNone(panel.find_parent('form'))
            tone = 'btn-primary' if panel_id == 'stage-register-confirm' else 'btn-danger'
            self.assertIn(tone, panel.select_one('button[type=submit]')['class'])

    def test_stage_registration_has_three_ordered_steps_and_single_guidance(self):
        self.collaboration.settings_json.update({
            'stage_url': 'https://vket.com/mypage/event/2026Winter/my_notification/new',
            'stage_registration_guidance': {'venue': '会場', 'tag': 'Vketステージ'},
        })
        self.collaboration.save(update_fields=['settings_json'])
        response, soup = self.page('status')
        card = soup.select_one('#stage-registration')
        steps = card.select('ol > li')
        self.assertEqual(len(steps), 3)
        self.assertEqual([step.h3.get_text(strip=True) for step in steps], [
            '文面とサムネイルを用意する', 'Vket のステージページで登録する',
            'Hub に登録したことを記録する',
        ])
        self.assertEqual(steps[0]['aria-current'], 'step')
        self.assertFalse(steps[1].has_attr('aria-current'))
        self.assertContains(response, '日程や発表内容が未確定でも、暫定の内容で登録できます。あとからも編集できます。', count=1)
        for text in ('Vketステージ登録画面での設定', '登録が終わったら、この画面に戻って',
                     'Vket側で登録しただけでは Hub の進捗は更新されません。'):
            self.assertNotContains(response, text)
        self.assertIsNone(card.select_one('.alert-info'))
        self.assertEqual([dt.text for dt in steps[1].select('dl dt')], ['開催会場', 'タグ'])
        self.assertEqual([dd.text for dd in steps[1].select('dl dd')], ['会場', 'Vketステージ'])
        copy = steps[0].select_one('a.btn-outline-primary')
        self.assertEqual(copy['href'], self.url('stage_copy'))
        self.assertEqual(copy.text, 'コピー用ページを開く')
        self.assertIsNotNone(steps[1].select_one('a.btn-primary[target=_blank][rel=noopener]'))
        self.assertIsNotNone(steps[1].select_one('a[href="https://vket.com/hub/2026Winter/notification"]'))
        self.assertEqual(steps[2].select_one('button[data-confirm-panel]').text, '登録したことを記録する')
        self.assertIn('btn-primary', steps[2].select_one('button[data-confirm-panel]')['class'])
        panel = steps[2].select_one('#stage-register-confirm.confirm-panel--primary')
        self.assertEqual(panel.p.text, 'Vket のステージページへの登録は終わりましたか？')

    def test_stage_thumbnail_completion_keeps_copy_link_and_advances_current_step(self):
        self.participation.stage_thumbnail_generated_at = timezone.now()
        self.participation.save(update_fields=['stage_thumbnail_generated_at'])
        _, soup = self.page('status')
        steps = soup.select('#stage-registration ol > li')
        self.assertIn('is-complete', steps[0]['class'])
        self.assertIsNotNone(steps[0].select_one('.stage-registration-number .fa-check'))
        self.assertIn('サムネイル作成済み', steps[0].h3.text)
        self.assertIsNone(steps[0].p)
        self.assertFalse(steps[0].has_attr('aria-current'))
        self.assertEqual(steps[1]['aria-current'], 'step')
        link = steps[0].select_one('a.btn-sm.btn-outline-primary')
        self.assertEqual(link['href'], self.url('stage_copy'))
        self.assertEqual(link.text, 'コピー用ページをもう一度開く')

    def test_registered_stage_links_use_derived_or_explicit_urls(self):
        self.participation.stage_registered_at = timezone.now()
        self.participation.save(update_fields=['stage_registered_at'])
        base = 'https://vket.com/mypage/event/2026Winter/my_notification'
        for settings, edit_url, list_url in (
            ({'stage_url': base + '/new'}, base, 'https://vket.com/hub/2026Winter/notification'),
            ({'stage_url': base + '/new/'}, base, 'https://vket.com/hub/2026Winter/notification'),
            ({'stage_url': base + '/new', 'stage_edit_url': ' https://example.com/edit ',
              'stage_list_url': ' https://example.com/list '}, 'https://example.com/edit', 'https://example.com/list'),
            ({'stage_url': 'https://example.com/unexpected'}, '', ''),
            ({'stage_url': base + '/new', 'stage_edit_url': 'javascript:alert(1)',
              'stage_list_url': 'data:text/html,unsafe'}, '', ''),
        ):
            for enabled in (True, False):
                with self.subTest(settings=settings, enabled=enabled):
                    self.collaboration.settings_json = {**settings, 'stage_registration_open': enabled}
                    self.collaboration.save(update_fields=['settings_json'])
                    response, soup = self.page('status')
                    card = soup.select_one('#stage-registration')
                    self.assertIsNone(card.ol)
                    self.assertIsNone(card.select_one('#stage-register-confirm'))
                    self.assertIsNotNone(card.select_one('.bg-success.bg-opacity-10'))
                    self.assertContains(response, 'Vket のステージページに登録済みです。登録した告知はあとからも編集できます。日程や発表内容が変わったら直してください。')
                    links = {a.get_text(strip=True): a for a in card.select('a')}
                    for label, url in (('Vket で告知を編集する', edit_url),
                                       ('ほかの集会の告知を見る（Vket のユーザーイベント一覧）', list_url)):
                        if url:
                            self.assertEqual(links[label]['href'], url)
                            self.assertEqual(links[label]['target'], '_blank')
                            self.assertIn('noopener', links[label]['rel'])
                        else:
                            self.assertNotIn(label, links)
                    self.assertEqual('コピー用の文面を開く' in links, enabled)
                    if enabled:
                        self.assertEqual(links['コピー用の文面を開く']['href'], self.url('stage_copy'))
                    self.assertNotContains(response, 'javascript:')
                    self.assertNotContains(response, 'data:text/html,unsafe')

    def test_stage_registration_without_url_keeps_fallback_message(self):
        response, soup = self.page('status')
        self.assertContains(response, '登録先URLは運営からのお知らせをご確認ください。')
        self.assertEqual(len(soup.select('#stage-registration ol > li')), 3)
        self.assertIsNone(soup.select_one('#stage-registration .stage-registration-list-link'))

    def test_closed_stage_registration_keeps_copy_and_stage_links_without_steps(self):
        self.collaboration.period_end = timezone.localdate() - timedelta(days=1)
        self.collaboration.settings_json['stage_url'] = 'https://example.com/stage'
        self.collaboration.save(update_fields=['period_end', 'settings_json'])
        _, soup = self.page('status')
        card = soup.select_one('#stage-registration')
        self.assertIsNone(card.ol)
        self.assertIsNone(card.select_one('#stage-register-confirm'))
        self.assertEqual(card.select_one(f'a[href="{self.url("stage_copy")}"]').text, 'コピー用ページを開く')
        self.assertIn('ステージページ', card.select_one('a[href="https://example.com/stage"]').text)

    def test_status_attention_disappears_after_completion(self):
        self.receipt.acknowledged_at = timezone.now()
        self.receipt.save(update_fields=['acknowledged_at'])
        self.participation.stage_registered_at = timezone.now()
        self.participation.save(update_fields=['stage_registered_at'])
        _, soup = self.page('status')
        self.assertIsNone(soup.select_one('#member-attention'))
        self.assertIn('登録済み', soup.select_one('#stage-registration').get_text())

    def test_copy_is_not_a_persistent_editor_and_keeps_generation_states(self):
        response, soup = self.page('stage_copy')
        self.assertIsNone(soup.select_one('[data-admin-action-bar], [data-admin-dirty], [data-admin-form-status]'))
        self.assertNotContains(response, '未保存')
        self.assertContains(response, 'この欄の変更は保存されません。', count=2)
        self.assertIsNotNone(soup.select_one('input#stage-title[readonly]'))
        self.assertIsNotNone(soup.select_one('a.setup-placeholder'))
        self.assertNotContains(response, 'id="stage-thumbnail-form"')
        self.community.poster_image = 'dummy-poster.jpg'
        self.community.save(update_fields=['poster_image'])
        _, soup = self.page('stage_copy')
        self.assertIsNotNone(soup.select_one('#stage-thumbnail-form'))
        self.participation.stage_thumbnail_requested_at = timezone.now()
        self.participation.save(update_fields=['stage_thumbnail_requested_at'])
        response, soup = self.page('stage_copy')
        self.assertContains(response, 'サムネイルを生成中です')
        self.assertIsNone(soup.select_one('#stage-thumbnail-form'))

    def test_notice_ack_is_completion_with_accordion_and_submit_once(self):
        response, soup = self.page('notice_list')
        self.assertIsNotNone(soup.select_one('#unackedAccordion .accordion-collapse.show'))
        form = soup.select_one('form.notice-ack-form.js-submit-once')
        self.assertEqual(form['action'], reverse('vket:notice_list_ack', kwargs={'pk': self.collaboration.pk, 'receipt_id': self.receipt.pk}))
        self.assertEqual(form.select_one('button').get_text(strip=True), '確認しました')
        self.assertContains(response, 'vket/js/submit_once.js')
        self.assertIsNone(soup.select_one('[data-admin-action-bar], [data-admin-dirty]'))

    def test_anonymous_token_ack_has_no_navigation_save_bar_or_token_in_html(self):
        self.client.logout()
        url = reverse('vket:ack_notice', kwargs={'ack_token': self.receipt.ack_token})
        response = self.client.get(url)
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'ta_hub/admin_base.html')
        self.assertEqual(soup.select_one('main h1').get_text(strip=True), f'Vket お知らせ確認: {self.community.name}')
        self.assertIsNone(soup.select_one('.admin-subnav, .community-selector, [data-admin-action-bar]'))
        self.assertIsNotNone(soup.select_one('form.js-submit-once button[type="submit"]'))
        self.assertNotContains(response, str(self.receipt.ack_token))
        self.assertNotContains(response, 'confirm(')
        result = self.client.post(url)
        self.assertEqual(result.status_code, 302)
        done = self.client.get(result.url)
        self.assertContains(done, '確認を記録しました')
        self.assertIsNone(BeautifulSoup(done.content, 'html.parser').select_one('form.js-submit-once'))

    def test_role_visibility_for_owner_staff_operator_and_superuser(self):
        operator = make_user(user_name='ui_operator', email='ui_operator@example.com', is_staff=True)
        superuser = make_user(user_name='ui_superuser', email='ui_superuser@example.com', is_staff=True, is_superuser=True)
        for user in (operator, superuser):
            CommunityMember.objects.create(community=self.community, user=user, role=CommunityMember.Role.STAFF)
        for user in (self.owner, self.other_user, operator, superuser):
            with self.subTest(user=user.user_name):
                self.client.force_login(user)
                self._set_active_community()
                _, soup = self.page('status')
                admin_link = soup.select_one(f'.admin-subnav a[href="{reverse("vket:manage", kwargs={"pk": self.collaboration.pk})}"]')
                self.assertEqual(bool(admin_link), user.is_staff or user.is_superuser)
                self.assertIsNotNone(soup.select_one(f'[data-confirm-panel="presentation-delete-{self.presentation.pk}"]'))
                self.page('apply')
                self.page('stage_copy')
                self.page('notice_list')

    def test_personal_account_cannot_see_community_data_or_modify_it(self):
        user = make_user(user_name='ui_personal', email='ui_personal@example.com')
        self.client.force_login(user)
        self._set_active_community()
        response, soup = self.page('status')
        self.assertNotContains(response, self.community.name)
        self.assertNotContains(response, self.receipt.notice.body)
        self.assertIsNone(soup.select_one('[data-confirm-panel]'))
        self.assertEqual(self.client.get(self.url('apply')).status_code, 403)
        self.assertEqual(self.client.get(self.url('stage_copy')).status_code, 403)
        response, soup = self.page('notice_list')
        self.assertContains(response, 'お知らせはまだありません。')
        self.assertIsNone(soup.select_one('form.notice-ack-form'))

    def test_read_only_member_cannot_submit_or_delete_but_operator_can(self):
        self.collaboration.phase = VketCollaboration.Phase.LOCKED
        self.collaboration.period_end = timezone.localdate() - timedelta(days=1)
        self.collaboration.save(update_fields=['phase', 'period_end'])
        response, soup = self.page('apply')
        self.assertIn('disabled', soup.select_one('button[form="vket-apply-form"]').attrs)
        self.assertIn('disabled', soup.select_one('[name="requested_start_time"]').attrs)
        self.assertNotContains(response, 'id="add-lt-btn"')
        self.assertContains(response, '受付期間外のため編集できません')
        _, soup = self.page('status')
        self.assertIsNone(soup.select_one('[data-confirm-panel^="presentation-delete-"]'))
        self.owner.is_staff = True
        self.owner.save(update_fields=['is_staff'])
        _, soup = self.page('apply')
        self.assertNotIn('disabled', soup.select_one('button[form="vket-apply-form"]').attrs)

    def test_unregistered_and_empty_notices_offer_existing_next_steps(self):
        self.participation.delete()
        response, soup = self.page('status')
        self.assertContains(response, 'まだ参加登録がありません')
        self.assertIn('要対応 1', soup.select_one('#member-attention').get_text())
        self.assertIsNotNone(soup.select_one(f'#member-attention a[href="{self.url("apply")}"]'))
        self.page('apply')
        response, soup = self.page('notice_list')
        self.assertContains(response, 'お知らせはまだありません。')
        self.assertIsNotNone(soup.select_one('.admin-empty-state'))
