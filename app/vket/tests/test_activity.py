"""申込み保存後の通知。実際の Webhook へは送らない。"""
from unittest import mock

from django.db import transaction
from django.urls import reverse

from vket.activity import activity_snapshot, notify_activity
from vket.forms import VketPresentationFormSet
from vket.models import VketParticipation

from .test_schedule_overlap import VketOverlapApplyBase


class VketActivityTests(VketOverlapApplyBase):
    # 保存・警告の共通フィクスチャを使い、通知だけを検証する。
    def setUp(self):
        super().setUp()
        self.url = 'https://discord.com/api/webhooks/123/token'
        self.collaboration.settings_json = {'activity_webhook_url': self.url}
        self.collaboration.save()
        self.send_patch = mock.patch('vket.activity.post_discord_webhook')
        self.send = self.send_patch.start()
        self.addCleanup(self.send_patch.stop)

    def _save_and_notify(self, *args, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return self._post_apply(*args, **kwargs)

    def _text(self):
        payload = self.send.call_args.args[1]
        return payload['content'] + '\n' + '\n'.join(e['description'] for e in payload['embeds'])

    def test_new_application_sends_once_after_commit_with_mentions_disabled(self):
        """成功した新規登録はコミット後に一回送信し、発表一覧と重なりも含む"""
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self._post_apply(rows=[{
                'speaker': '@everyone 発表者', 'theme': '技術の話', 'lt_start_time': '21:45',
            }])
            self.send.assert_not_called()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(callbacks), 1)
        self.send.assert_called_once()
        payload = self.send.call_args.args[1]
        self.assertEqual(self.send.call_args.args[0], self.url)
        self.assertEqual(payload['allowed_mentions'], {'parse': []})
        text = self._text()
        for value in ('個人開発集会', '新規の申込み', '@everyone 発表者', '技術の話', '21:45', 'ゲーム開発集会'):
            self.assertIn(value, text)
        self.assertIn(self.today.strftime('%Y/%m/%d'), text)

    def test_precreated_participation_first_application_is_notified_as_new(self):
        """運営が参加行を用意していても、初回の申込みは新規として通知する"""
        VketParticipation.objects.create(
            collaboration=self.collaboration, community=self.community,
            requested_date=self.today, requested_start_time='21:00', requested_duration=90,
        )
        response = self._save_and_notify()
        self.assertEqual(response.status_code, 302)
        self.send.assert_called_once()
        self.assertIn('新規の申込み', self._text())

    def test_no_changes_do_not_send(self):
        self._save_and_notify()
        self.send.reset_mock()
        self._save_and_notify()
        self.send.assert_not_called()

    def test_schedule_change_sends_once(self):
        self._save_and_notify()
        self.send.reset_mock()
        response = self._save_and_notify(start='20:00')
        self.assertEqual(response.status_code, 302)
        self.send.assert_called_once()
        self.assertIn('日程の変更', self._text())
        self.assertIn('20:00', self._text())

    def test_presentation_add_change_and_withdraw_each_send_once(self):
        self._save_and_notify()
        cases = [
            ([{'speaker': '発表者', 'theme': '技術の話', 'lt_start_time': '21:45'},
              {'speaker': '追加者', 'theme': '追加の話', 'lt_start_time': '22:15'}], '発表の追加'),
            ([{'speaker': '発表者', 'theme': '変更した話', 'lt_start_time': '21:45'},
              {'speaker': '追加者', 'theme': '追加の話', 'lt_start_time': '22:15'}], '発表の変更'),
            ([{'speaker': '発表者', 'theme': '変更した話', 'lt_start_time': '21:45'},
              {'speaker': '追加者', 'theme': '追加の話', 'DELETE': True}], '発表の取り下げ'),
        ]
        for rows, operation in cases:
            with self.subTest(operation=operation):
                self.send.reset_mock()
                response = self._save_and_notify(rows=rows)
                self.assertEqual(response.status_code, 302)
                self.send.assert_called_once()
                self.assertIn(operation, self._text())
        self.assertNotIn('追加者', self.send.call_args.args[1]['embeds'][0]['description'])

    def test_unset_empty_invalid_or_non_string_setting_does_not_send(self):
        for setting in (None, [], {}, {'activity_webhook_url': ''}, {'activity_webhook_url': 123},
                        {'activity_webhook_url': 'https://example.com/webhook'},
                        {'activity_webhook_url': 'https://discord.com/api/webhooks/'},
                        {'activity_webhook_url': self.url + '?bad=1'}):
            with self.subTest(setting=setting):
                self.collaboration.settings_json = setting
                self.collaboration.save()
                own = self._own_participation()
                if own:
                    own.delete()
                response = self._save_and_notify()
                self.assertEqual(response.status_code, 302)
                self.send.assert_not_called()

    def test_failed_send_keeps_saved_application_and_omits_url_from_logs(self):
        self.send.side_effect = RuntimeError('request failed: ' + self.url)
        with self.assertLogs('vket.activity', level='WARNING') as logs:
            response = self._save_and_notify()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self._own_participation().presentations.exists())
        self.assertNotIn(self.url, ' '.join(logs.output))
        self.assertEqual(logs.records[0].result, 'failed')
        self.assertEqual(logs.records[0].error_type, 'RuntimeError')

    def test_invalid_form_does_not_send(self):
        response = self._save_and_notify(start='invalid')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self._own_participation())
        self.send.assert_not_called()

    def test_rollback_does_not_send(self):
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    self._post_apply()
                    raise RuntimeError('rollback')
            except RuntimeError:
                pass
        self.send.assert_not_called()
        self.assertIsNone(self._own_participation())

    def test_withdrawal_operation_is_in_notification(self):
        self._save_and_notify()
        own = self._own_participation()
        before = activity_snapshot(own)
        own.lifecycle = VketParticipation.Lifecycle.WITHDRAWN
        own.save()
        self.send.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            notify_activity(self.collaboration, self.community.name, before, activity_snapshot(own), [])
        self.send.assert_called_once()
        self.assertIn('辞退', self._text())

    def test_admin_update_does_not_send_application_activity(self):
        self._save_and_notify()
        own = self._own_participation()
        self.owner.is_staff = True
        self.owner.save()
        self.send.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('vket:manage_participation_update', kwargs={
                'pk': self.collaboration.pk, 'participation_id': own.pk,
            }), {'lifecycle': 'withdrawn'})
        self.assertEqual(response.status_code, 302)
        self.send.assert_not_called()

    def test_twenty_presentations_fit_in_one_discord_payload(self):
        """最大長・最大件数でも、書式記号のエスケープ後に通知上限を超えない"""
        max_num = VketPresentationFormSet.max_num
        fields = VketPresentationFormSet.form.base_fields
        name_length = self.community._meta.get_field('name').max_length
        for character in ('人', '*', '\\'):
            with self.subTest(character=character):
                self.community.name = character * name_length
                self.community.save()
                self.other.community.name = character * name_length
                self.other.community.save()
                self.send.reset_mock()
                rows = [{
                    'speaker': character * fields['speaker'].max_length,
                    'theme': character * fields['theme'].max_length,
                    'lt_start_time': '21:45',
                } for _ in range(max_num)]
                response = self._save_and_notify(rows=rows)
                self.assertEqual(response.status_code, 302)
                self.send.assert_called_once()
                payload = self.send.call_args.args[1]
                self.assertLessEqual(len(payload['content']), 2000)
                self.assertEqual(len(payload['embeds']), 2)
                self.assertLessEqual(sum(len(e['title']) + len(e['description']) for e in payload['embeds']), 6000)
                for embed in payload['embeds']:
                    self.assertLessEqual(len(embed['description']), 4096)
                description = payload['embeds'][0]['description']
                included = sum(line.startswith('- ') for line in description.splitlines())
                if character == '人':
                    self.assertEqual(included, max_num)
                else:
                    self.assertGreater(included, 0)
                    self.assertLess(included, max_num)
                    self.assertTrue(description.endswith(f'ほか {max_num - included} 件'))
                warning = payload['embeds'][1]['description']
                included_pairs = sum(line.startswith('- ') for line in warning.splitlines())
                self.assertTrue(warning.endswith(f'ほか {max_num - included_pairs} 件'))

    def test_more_than_max_presentations_is_a_form_error_and_does_not_send(self):
        """画面の最大件数を超える送信は、保存せずフォームエラーにする"""
        rows = [{'speaker': '発表者', 'theme': '発表'} for _ in range(VketPresentationFormSet.max_num + 1)]
        response = self._save_and_notify(rows=rows)
        self.assertEqual(response.status_code, 200)
        errors = response.context['formset'].non_form_errors().as_data()
        self.assertEqual([error.code for error in errors], ['too_many_forms'])
        self.assertIsNone(self._own_participation())
        self.send.assert_not_called()

    def test_deleted_presentations_do_not_count_towards_formset_max(self):
        """削除行を含む送信は、残る発表の件数で上限を判定する"""
        rows = [{'speaker': '発表者', 'theme': '発表'} for _ in range(VketPresentationFormSet.max_num)]
        rows.append({'speaker': '削除者', 'theme': '削除', 'DELETE': True})
        response = self._save_and_notify(rows=rows)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._own_participation().presentations.count(), VketPresentationFormSet.max_num)

    def test_free_input_is_escaped_in_content_presentations_and_overlap(self):
        """集会名・登壇者名・テーマ・重なり欄の入力をリンクや強調にしない"""
        value = r'[x](https://example.com) **x** \\ _ ~ ` | > # -'
        escaped = r'\[x\]\(https://example.com\) \*\*x\*\* \\\\ \_ \~ \` \| \> \# \-'
        self.community.name = value
        self.community.save()
        self.other.community.name = value
        self.other.community.save()
        response = self._save_and_notify(rows=[{
            'speaker': value, 'theme': value, 'lt_start_time': '21:45',
        }])
        self.assertEqual(response.status_code, 302)
        payload = self.send.call_args.args[1]
        self.assertIn(f'**{escaped}**', payload['content'])
        self.assertIn(f'- {escaped} / {escaped} / ', payload['embeds'][0]['description'])
        self.assertIn(escaped, payload['embeds'][1]['description'])
        self.assertNotIn(value, self._text())

    def test_single_oversized_overlap_line_is_omitted_without_breaking_escape(self):
        """一行だけで上限を超える重なりは、入力の途中で切らず件数だけ示す"""
        self._post_apply()
        with self.captureOnCommitCallbacks(execute=True):
            notify_activity(
                self.collaboration, self.community.name, None,
                activity_snapshot(self._own_participation()), ['*' * 4096],
            )
        self.assertEqual(self.send.call_args.args[1]['embeds'][1]['description'], 'ほか 1 件')
