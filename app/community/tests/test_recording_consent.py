"""集会の撮影許可・撮影ステータスの初期値の設定と、発表申請テンプレートの既定文のテスト。"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from community.constants import DEFAULT_LT_APPLICATION_TEMPLATE, RecordingPolicy
from tests.factories import make_community, make_community_member, make_discord_linked_user

LEGACY_DEFAULT_TEMPLATE = "【発表概要】\n\n【スライド公開】OK / NG\n\n【動画撮影】YouTube公開 / Discord限定 / OK / NG"


def migrate_to_latest():
    """migration テストの後片付け。後から足された migration も含めて最新まで戻す。"""
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


class CommunityRecordingAllowedDefaultTest(TestCase):
    """Community.recording_allowed の既定値。"""

    def test_default_is_false(self):
        """新しい集会は撮影を許可しない状態で作られる（オプトイン方式）。"""
        community = make_community(name='既定値の集会')

        community.refresh_from_db()
        self.assertFalse(community.recording_allowed)

    def test_model_and_database_defaults_are_false(self):
        """Python と DB の既定値をどちらも撮影しない側にそろえる。"""
        from community.models import Community

        field = Community._meta.get_field('recording_allowed')
        self.assertIs(field.default, False)
        self.assertIs(field.db_default, False)


class RecordingAllowedSettingsViewTest(TestCase):
    """集会設定画面での撮影許可の表示と保存。"""

    def setUp(self):
        self.owner = make_discord_linked_user(user_name='rec_owner', email='rec_owner@example.com')
        self.community = make_community(name='撮影設定の集会', owner=self.owner, recording_allowed=True)
        self.url = reverse('community:update_lt_settings', kwargs={'pk': self.community.pk})

    def _post(self, **extra):
        data = {
            'accepts_lt_application': 'on',
            'lt_application_template': '【発表概要】',
            'default_lt_duration': '30',
            'lt_start_offset_minutes': '30',
        }
        data.update(extra)
        return self.client.post(self.url, data)

    def test_settings_page_shows_allowed_radio_checked(self):
        """設定画面の撮影許可は 2 択で、「許可する」が選ばれている。"""
        self.client.force_login(self.owner)

        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ハブの自動撮影の対象になりません')
        html = response.content.decode()
        self.assertRegex(html, r'id="recording_allowed_true" name="recording_allowed" value="true"\s+checked')
        self.assertRegex(html, r'id="recording_allowed_false" name="recording_allowed" value="false"\s+onchange')

    def test_turn_off_recording(self):
        """「許可しない」を選んで保存すると撮影許可がオフになる。"""
        self.client.force_login(self.owner)

        response = self._post(recording_allowed='false')

        self.assertRedirects(response, reverse('community:settings'))
        self.community.refresh_from_db()
        self.assertFalse(self.community.recording_allowed)

    def test_turn_on_recording(self):
        """「許可する」を選んで保存すると撮影許可がオンに戻る。"""
        self.community.recording_allowed = False
        self.community.save(update_fields=['recording_allowed'])
        self.client.force_login(self.owner)

        self._post(recording_allowed='true')

        self.community.refresh_from_db()
        self.assertTrue(self.community.recording_allowed)

    def test_legacy_switch_values_are_accepted(self):
        """スイッチだった頃の画面の送信（オン = 'on'、オフ = 未送信）も同じ意味で受け付ける。"""
        self.client.force_login(self.owner)

        self._post()
        self.community.refresh_from_db()
        self.assertFalse(self.community.recording_allowed)

        self._post(recording_allowed='on')

        self.community.refresh_from_db()
        self.assertTrue(self.community.recording_allowed)

    def test_recording_switch_is_independent_from_accepting_applications(self):
        """発表申請を受け付けない集会でも撮影許可を切り替えられる。"""
        self.community.accepts_lt_application = False
        self.community.recording_allowed = False
        self.community.save(update_fields=['accepts_lt_application', 'recording_allowed'])
        staff = make_discord_linked_user(user_name='rec_staff', email='rec_staff@example.com')
        make_community_member(self.community, staff)
        self.client.force_login(staff)

        # 申請受付オフで隠れる詳細設定ブロックの外に置かれている
        html = self.client.get(reverse('community:settings')).content.decode()
        self.assertLess(html.index('name="recording_allowed"'), html.index('id="lt-settings-detail"'))

        self.client.post(self.url, {'recording_allowed': 'true'})

        self.community.refresh_from_db()
        self.assertFalse(self.community.accepts_lt_application)
        self.assertTrue(self.community.recording_allowed)


class DefaultRecordingPolicySettingsTest(TestCase):
    """集会設定の「撮影ステータスの初期値」（3 択）。"""

    def setUp(self):
        self.owner = make_discord_linked_user(user_name='def_owner', email='def_owner@example.com')
        self.community = make_community(name='既定ステータスの集会', owner=self.owner, recording_allowed=True)
        self.url = reverse('community:update_lt_settings', kwargs={'pk': self.community.pk})
        self.client.force_login(self.owner)

    def _post(self, **extra):
        data = {'recording_allowed': 'true', 'default_lt_duration': '30', 'lt_start_offset_minutes': '30'}
        data.update(extra)
        return self.client.post(self.url, data)

    def test_default_is_public(self):
        """新しい集会の撮影ステータスの初期値は「公開」。"""
        self.community.refresh_from_db()

        self.assertEqual(self.community.default_recording_policy, RecordingPolicy.PUBLIC)

    def test_settings_page_shows_three_choices_with_public_checked(self):
        """設定画面に 3 択が出て、「公開」が選ばれている。"""
        html = self.client.get(reverse('community:settings')).content.decode()

        for value, label in RecordingPolicy.choices:
            self.assertIn(f'id="default_recording_policy_{value}"', html)
            self.assertIn(label, html)
        self.assertRegex(html, r'id="default_recording_policy_public" name="default_recording_policy" value="public"\s+checked')
        self.assertNotRegex(html, r'id="default_recording_policy_forbidden"[^>]*checked')
        self.assertIn('撮影ステータスの初期値', html)
        self.assertNotIn('デフォルトの撮影ステータス', html)

    def test_each_choice_is_saved(self):
        """3 つの値それぞれを保存できる。"""
        for value in RecordingPolicy.values:
            with self.subTest(value=value):
                response = self._post(default_recording_policy=value)

                self.assertRedirects(response, reverse('community:settings'))
                self.community.refresh_from_db()
                self.assertEqual(self.community.default_recording_policy, value)

    def test_invalid_or_missing_value_keeps_current(self):
        """選択肢外・未送信の時は今の値を保つ。"""
        self._post(default_recording_policy='allowed')

        self._post(default_recording_policy='secret')
        self.community.refresh_from_db()
        self.assertEqual(self.community.default_recording_policy, RecordingPolicy.ALLOWED)

        self._post()
        self.community.refresh_from_db()
        self.assertEqual(self.community.default_recording_policy, RecordingPolicy.ALLOWED)

    def test_allowed_and_default_are_saved_together(self):
        """撮影許可（2 択）と撮影ステータスの初期値（3 択）は 1 回の保存で両方入る。"""
        self._post(recording_allowed='false', default_recording_policy='forbidden')

        self.community.refresh_from_db()
        self.assertFalse(self.community.recording_allowed)
        self.assertEqual(self.community.default_recording_policy, RecordingPolicy.FORBIDDEN)

    def _disallow(self, policy=RecordingPolicy.FORBIDDEN):
        self.community.recording_allowed = False
        self.community.default_recording_policy = policy
        self.community.save(update_fields=['recording_allowed', 'default_recording_policy'])

    def test_switching_to_allowed_turns_forbidden_into_public(self):
        """「許可しない」から「許可する」に切り替えた時、「禁止」のままなら「公開」にする。"""
        self._disallow()

        self._post(default_recording_policy='forbidden')

        self.community.refresh_from_db()
        self.assertTrue(self.community.recording_allowed)
        self.assertEqual(self.community.default_recording_policy, RecordingPolicy.PUBLIC)

    def test_switching_to_allowed_keeps_forbidden_chosen_by_organizer(self):
        """切り替えと同時に主催者が「禁止」を選び直して保存したら、上書きしない。"""
        self._disallow()

        self._post(default_recording_policy='forbidden', default_recording_policy_chosen='1')

        self.community.refresh_from_db()
        self.assertTrue(self.community.recording_allowed)
        self.assertEqual(self.community.default_recording_policy, RecordingPolicy.FORBIDDEN)

    def test_settings_page_marks_choice_made_by_organizer(self):
        """設定画面は、主催者が初期値を選んだ時だけ印を立てる hidden を送る。"""
        html = self.client.get(reverse('community:settings')).content.decode()

        self.assertIn('name="default_recording_policy_chosen" value=""', html)
        self.assertIn('onchange="markRecordingPolicyTouched()"', html)
        # 戻る操作でフォームが復元された後の送信でも印を立てる
        self.assertIn("chosen.form.addEventListener('submit'", html)

    def test_switching_to_allowed_without_value_becomes_public(self):
        """切り替え時にデフォルトが送られなくても「公開」にする。"""
        self._disallow()

        self._post()

        self.community.refresh_from_db()
        self.assertEqual(self.community.default_recording_policy, RecordingPolicy.PUBLIC)

    def test_switching_to_allowed_without_value_ignores_previous_allowed(self):
        """許可しない間に「許可」が残っていても、切り替え時に未送信なら「公開」にする。"""
        self._disallow(RecordingPolicy.ALLOWED)

        self._post()

        self.community.refresh_from_db()
        self.assertEqual(self.community.default_recording_policy, RecordingPolicy.PUBLIC)

    def test_switching_to_allowed_keeps_allowed_choice(self):
        """切り替え時に「許可」を選んでいればそのまま保存する。"""
        self._disallow()

        self._post(default_recording_policy='allowed')

        self.community.refresh_from_db()
        self.assertEqual(self.community.default_recording_policy, RecordingPolicy.ALLOWED)

    def test_explicit_forbidden_is_kept_when_already_allowed(self):
        """すでに「許可する」の集会で明示的に「禁止」を保存したら、次に保存しても上書きしない。"""
        self._post(default_recording_policy='forbidden')
        self._post(default_recording_policy='forbidden')

        self.community.refresh_from_db()
        self.assertTrue(self.community.recording_allowed)
        self.assertEqual(self.community.default_recording_policy, RecordingPolicy.FORBIDDEN)

    def test_settings_page_selects_public_when_switching_to_allowed(self):
        """設定画面の JS は「許可する」に切り替えた時に「公開」を選ぶ。"""
        html = self.client.get(reverse('community:settings')).content.decode()

        self.assertIn("document.getElementById('default_recording_policy_public').checked = true", html)

    def test_default_section_is_hidden_when_not_allowed(self):
        """「許可しない」の集会では、撮影ステータスの初期値を畳んで表示する。"""
        self.community.recording_allowed = False
        self.community.save(update_fields=['recording_allowed'])

        html = self.client.get(reverse('community:settings')).content.decode()

        self.assertIn('id="default-recording-policy-section" style="display: none;"', html)


class DefaultLTApplicationTemplateTest(TestCase):
    """設定画面に出す発表申請テンプレートの既定文。"""

    def test_default_template_has_no_recording_line(self):
        """既定文から撮影の項目が消えている。"""
        self.assertEqual(DEFAULT_LT_APPLICATION_TEMPLATE, "【発表概要】\n\n【スライド公開】OK / NG")
        self.assertNotIn('動画撮影', DEFAULT_LT_APPLICATION_TEMPLATE)

    def test_settings_page_shows_new_default_when_template_is_empty(self):
        """テンプレート未設定の集会では新しい既定文を表示する。"""
        owner = make_discord_linked_user(user_name='tpl_owner', email='tpl_owner@example.com')
        make_community(name='テンプレ未設定', owner=owner)
        self.client.force_login(owner)

        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.context['lt_application_template_display'], DEFAULT_LT_APPLICATION_TEMPLATE)
        self.assertNotContains(response, '【動画撮影】')


class RemoveRecordingLineMigrationTest(TransactionTestCase):
    """旧既定文と完全一致するテンプレートだけを置き換えるデータ migration。"""

    migrate_from = [('community', '0029_community_recording_allowed')]
    migrate_to = [('community', '0030_remove_recording_line_from_default_lt_template')]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        self.old_apps = executor.loader.project_state(self.migrate_from).apps

    def _make(self, name, template):
        Community = self.old_apps.get_model('community', 'Community')
        return Community.objects.create(
            name=name,
            frequency='毎週',
            organizers='主催',
            lt_application_template=template,
        ).pk

    def tearDown(self):
        migrate_to_latest()
        super().tearDown()

    def _community_model(self, targets):
        return MigrationExecutor(connection).loader.project_state(targets).apps.get_model('community', 'Community')

    def _template_after_migration(self, pk):
        MigrationExecutor(connection).migrate(self.migrate_to)
        return self._community_model(self.migrate_to).objects.get(pk=pk).lt_application_template

    def test_replaces_exact_legacy_default(self):
        """旧既定文のまま保存した集会は新しい既定文になる。"""
        pk = self._make('旧既定文', LEGACY_DEFAULT_TEMPLATE)

        self.assertEqual(self._template_after_migration(pk), "【発表概要】\n\n【スライド公開】OK / NG")

    def test_keeps_hand_edited_template(self):
        """手で書き換えたテンプレートは撮影の行が残っていても触らない。"""
        edited = LEGACY_DEFAULT_TEMPLATE + "\n\n【対象者】"
        pk = self._make('手書き', edited)

        self.assertEqual(self._template_after_migration(pk), edited)

    def test_keeps_empty_template(self):
        """未設定（空）のテンプレートは空のまま。"""
        pk = self._make('未設定', '')

        self.assertEqual(self._template_after_migration(pk), '')

    def test_replaces_legacy_default_saved_with_crlf(self):
        """CRLF（や CR）で保存された旧既定文も置き換える。"""
        crlf_pk = self._make('CRLF', LEGACY_DEFAULT_TEMPLATE.replace('\n', '\r\n'))
        cr_pk = self._make('CR', LEGACY_DEFAULT_TEMPLATE.replace('\n', '\r'))

        self.assertEqual(self._template_after_migration(crlf_pk), DEFAULT_LT_APPLICATION_TEMPLATE)
        Community = self._community_model(self.migrate_to)
        self.assertEqual(Community.objects.get(pk=cr_pk).lt_application_template, DEFAULT_LT_APPLICATION_TEMPLATE)

    def test_does_not_rely_on_collation_for_exact_match(self):
        """大文字小文字・末尾空白だけ違うテンプレートは置き換えない。"""
        lower = LEGACY_DEFAULT_TEMPLATE.replace('OK / NG', 'ok / ng')
        trailing = LEGACY_DEFAULT_TEMPLATE + ' '
        lower_pk = self._make('小文字', lower)
        trailing_pk = self._make('末尾空白', trailing)

        self.assertEqual(self._template_after_migration(lower_pk), lower)
        Community = self._community_model(self.migrate_to)
        self.assertEqual(Community.objects.get(pk=trailing_pk).lt_application_template, trailing)

    def test_reverse_restores_only_exact_new_default(self):
        """逆方向は新しい既定文と完全一致する集会だけ旧既定文に戻す。"""
        MigrationExecutor(connection).migrate(self.migrate_to)
        Community = self._community_model(self.migrate_to)

        def make(name, template):
            return Community.objects.create(
                name=name, frequency='毎週', organizers='主催', lt_application_template=template,
            ).pk

        new_default_pk = make('新既定文', DEFAULT_LT_APPLICATION_TEMPLATE)
        edited = DEFAULT_LT_APPLICATION_TEMPLATE + '\n\n【対象者】'
        edited_pk = make('手書き', edited)
        crlf_pk = make('CRLF', DEFAULT_LT_APPLICATION_TEMPLATE.replace('\n', '\r\n'))

        MigrationExecutor(connection).migrate(self.migrate_from)

        self.assertEqual(Community.objects.get(pk=new_default_pk).lt_application_template, LEGACY_DEFAULT_TEMPLATE)
        self.assertEqual(Community.objects.get(pk=edited_pk).lt_application_template, edited)
        self.assertEqual(Community.objects.get(pk=crlf_pk).lt_application_template, LEGACY_DEFAULT_TEMPLATE)


class RecordingAllowedDbDefaultTest(TransactionTestCase):
    """migration 適用後に動く旧リビジョン（列を知らないコード）の INSERT が通る。"""

    def test_old_model_insert_gets_db_default(self):
        """recording_allowed を知らない旧モデルで作っても、DB 既定値で False になる。"""
        old_state = MigrationExecutor(connection).loader.project_state(
            [('community', '0028_alter_community_default_lt_duration')],
        )
        OldCommunity = old_state.apps.get_model('community', 'Community')
        self.assertNotIn('recording_allowed', [f.name for f in OldCommunity._meta.get_fields()])

        pk = OldCommunity.objects.create(name='旧リビジョン', frequency='毎週', organizers='主催').pk

        from community.models import Community
        self.assertFalse(Community.objects.get(pk=pk).recording_allowed)


class RecordingOptInDefaultMigrationTest(TransactionTestCase):
    """0032 は既存の許可・不許可と撮影ステータスの初期値を変えない。"""

    migrate_from = [('community', '0031_community_default_recording_policy')]
    migrate_to = [('community', '0032_alter_community_recording_allowed_default')]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        self.old_model = executor.loader.project_state(self.migrate_from).apps.get_model('community', 'Community')

    def tearDown(self):
        migrate_to_latest()
        super().tearDown()

    def test_preserves_existing_values_and_changes_new_row_default(self):
        allowed = self.old_model.objects.create(name='許可済み', frequency='毎週', organizers='主催')
        disallowed = self.old_model.objects.create(
            name='不許可', frequency='毎週', organizers='主催',
            recording_allowed=False, default_recording_policy=RecordingPolicy.ALLOWED,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        model = executor.loader.project_state(self.migrate_to).apps.get_model('community', 'Community')

        self.assertTrue(model.objects.get(pk=allowed.pk).recording_allowed)
        self.assertEqual(model.objects.get(pk=allowed.pk).default_recording_policy, RecordingPolicy.PUBLIC)
        self.assertFalse(model.objects.get(pk=disallowed.pk).recording_allowed)
        self.assertEqual(model.objects.get(pk=disallowed.pk).default_recording_policy, RecordingPolicy.ALLOWED)
        created = model.objects.create(name='新しい集会', frequency='毎週', organizers='主催')
        self.assertFalse(created.recording_allowed)
        self.assertEqual(created.default_recording_policy, RecordingPolicy.PUBLIC)


class DefaultRecordingPolicyMigrationTest(TransactionTestCase):
    """0031: 既存の集会の撮影ステータスの初期値を、今の撮影許可と矛盾しない値で埋める。"""

    migrate_from = [('community', '0030_remove_recording_line_from_default_lt_template')]
    migrate_to = [('community', '0031_community_default_recording_policy')]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        self.old_apps = executor.loader.project_state(self.migrate_from).apps

    def tearDown(self):
        migrate_to_latest()
        super().tearDown()

    def _make(self, name, recording_allowed):
        Community = self.old_apps.get_model('community', 'Community')
        return Community.objects.create(
            name=name, frequency='毎週', organizers='主催', recording_allowed=recording_allowed,
        ).pk

    def _migrated(self, pk):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        Community = executor.loader.project_state(self.migrate_to).apps.get_model('community', 'Community')
        return Community.objects.get(pk=pk)

    def test_allowed_community_defaults_to_public(self):
        """撮影を許可していた集会は「許可する / 公開」。"""
        community = self._migrated(self._make('許可', True))

        self.assertTrue(community.recording_allowed)
        self.assertEqual(community.default_recording_policy, RecordingPolicy.PUBLIC)

    def test_disallowed_community_defaults_to_forbidden(self):
        """撮影を許可していなかった集会は「許可しない / 禁止」。"""
        allowed_pk = self._make('許可', True)
        community = self._migrated(self._make('不許可', False))

        self.assertFalse(community.recording_allowed)
        self.assertEqual(community.default_recording_policy, RecordingPolicy.FORBIDDEN)
        Community = MigrationExecutor(connection).loader.project_state(self.migrate_to).apps.get_model(
            'community', 'Community',
        )
        self.assertEqual(Community.objects.get(pk=allowed_pk).default_recording_policy, RecordingPolicy.PUBLIC)

    def test_reverse_drops_column(self):
        """逆方向に戻しても失敗しない（列ごと消える）。"""
        pk = self._make('往復', False)
        self._migrated(pk)

        MigrationExecutor(connection).migrate(self.migrate_from)

        Community = self.old_apps.get_model('community', 'Community')
        self.assertFalse(Community.objects.get(pk=pk).recording_allowed)


class DefaultRecordingPolicyDbDefaultTest(TransactionTestCase):
    """migration 適用後に動く旧リビジョン（列を知らないコード）の INSERT が通る。"""

    def test_old_model_insert_gets_public(self):
        """default_recording_policy を知らない旧モデルで作っても、DB 既定値で「公開」になる。"""
        old_state = MigrationExecutor(connection).loader.project_state(
            [('community', '0030_remove_recording_line_from_default_lt_template')],
        )
        OldCommunity = old_state.apps.get_model('community', 'Community')
        self.assertNotIn('default_recording_policy', [f.name for f in OldCommunity._meta.get_fields()])

        pk = OldCommunity.objects.create(name='旧リビジョン', frequency='毎週', organizers='主催').pk

        from community.models import Community
        self.assertEqual(Community.objects.get(pk=pk).default_recording_policy, RecordingPolicy.PUBLIC)
