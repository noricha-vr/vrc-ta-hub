"""集会の「撮影を許可する」設定と、発表申請テンプレートの既定文のテスト。"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from community.constants import DEFAULT_LT_APPLICATION_TEMPLATE
from tests.factories import make_community, make_community_member, make_discord_linked_user

LEGACY_DEFAULT_TEMPLATE = "【発表概要】\n\n【スライド公開】OK / NG\n\n【動画撮影】YouTube公開 / Discord限定 / OK / NG"


class CommunityRecordingAllowedDefaultTest(TestCase):
    """Community.recording_allowed の既定値。"""

    def test_default_is_true(self):
        """新しい集会は撮影を許可した状態で作られる（オプトアウト方式）。"""
        community = make_community(name='既定値の集会')

        community.refresh_from_db()
        self.assertTrue(community.recording_allowed)


class RecordingAllowedSettingsViewTest(TestCase):
    """集会設定画面での撮影許可の表示と保存。"""

    def setUp(self):
        self.owner = make_discord_linked_user(user_name='rec_owner', email='rec_owner@example.com')
        self.community = make_community(name='撮影設定の集会', owner=self.owner)
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

    def test_settings_page_shows_checked_switch(self):
        """設定画面に撮影許可のスイッチがオンで表示される。"""
        self.client.force_login(self.owner)

        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="recording_allowed"')
        self.assertContains(response, 'ハブの自動撮影の対象になりません')
        self.assertRegex(
            response.content.decode(),
            r'id="recording_allowed" name="recording_allowed"\s+checked',
        )

    def test_turn_off_recording(self):
        """スイッチを外して保存すると撮影許可がオフになる。"""
        self.client.force_login(self.owner)

        response = self._post()

        self.assertRedirects(response, reverse('community:settings'))
        self.community.refresh_from_db()
        self.assertFalse(self.community.recording_allowed)

    def test_turn_on_recording(self):
        """スイッチを入れて保存すると撮影許可がオンに戻る。"""
        self.community.recording_allowed = False
        self.community.save(update_fields=['recording_allowed'])
        self.client.force_login(self.owner)

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

        self.client.post(self.url, {'recording_allowed': 'on'})

        self.community.refresh_from_db()
        self.assertFalse(self.community.accepts_lt_application)
        self.assertTrue(self.community.recording_allowed)


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

    def tearDown(self):
        MigrationExecutor(connection).migrate(self.migrate_to)
        super().tearDown()

    def _make(self, name, template):
        Community = self.old_apps.get_model('community', 'Community')
        return Community.objects.create(
            name=name,
            frequency='毎週',
            organizers='主催',
            lt_application_template=template,
        ).pk

    def _template_after_migration(self, pk):
        MigrationExecutor(connection).migrate(self.migrate_to)
        from community.models import Community
        return Community.objects.get(pk=pk).lt_application_template

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

    def test_does_not_rely_on_collation_for_exact_match(self):
        """大文字小文字・末尾空白だけ違うテンプレートは置き換えない。"""
        lower = LEGACY_DEFAULT_TEMPLATE.replace('OK / NG', 'ok / ng')
        trailing = LEGACY_DEFAULT_TEMPLATE + ' '
        lower_pk = self._make('小文字', lower)
        trailing_pk = self._make('末尾空白', trailing)

        self.assertEqual(self._template_after_migration(lower_pk), lower)
        from community.models import Community
        self.assertEqual(Community.objects.get(pk=trailing_pk).lt_application_template, trailing)

    def test_reverse_restores_only_exact_new_default(self):
        """逆方向は新しい既定文と完全一致する集会だけ旧既定文に戻す。"""
        from community.models import Community

        MigrationExecutor(connection).migrate(self.migrate_to)
        new_default_pk = make_community(
            name='新既定文', lt_application_template=DEFAULT_LT_APPLICATION_TEMPLATE,
        ).pk
        edited = DEFAULT_LT_APPLICATION_TEMPLATE + '\n\n【対象者】'
        edited_pk = make_community(name='手書き', lt_application_template=edited).pk

        MigrationExecutor(connection).migrate(self.migrate_from)

        self.assertEqual(Community.objects.get(pk=new_default_pk).lt_application_template, LEGACY_DEFAULT_TEMPLATE)
        self.assertEqual(Community.objects.get(pk=edited_pk).lt_application_template, edited)


class RecordingAllowedDbDefaultTest(TransactionTestCase):
    """migration 適用後に動く旧リビジョン（列を知らないコード）の INSERT が通る。"""

    def test_old_model_insert_gets_db_default(self):
        """recording_allowed を知らない旧モデルで作っても、DB 既定値で True になる。"""
        old_state = MigrationExecutor(connection).loader.project_state(
            [('community', '0028_alter_community_default_lt_duration')],
        )
        OldCommunity = old_state.apps.get_model('community', 'Community')
        self.assertNotIn('recording_allowed', [f.name for f in OldCommunity._meta.get_fields()])

        pk = OldCommunity.objects.create(name='旧リビジョン', frequency='毎週', organizers='主催').pk

        from community.models import Community
        self.assertTrue(Community.objects.get(pk=pk).recording_allowed)
