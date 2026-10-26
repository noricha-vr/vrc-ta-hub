"""集会登録機能のテスト."""
from unittest.mock import patch, MagicMock

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse

from community.constants import RecordingPolicy
from community.forms import CommunityCreateForm, CommunityUpdateForm
from community.models import Community, CommunityMember
from tests.factories import make_community
from user_account.models import CustomUser


# テスト用のSOCIALACCOUNT_PROVIDERS設定（APPSなし）
# これにより、データベースのSocialAppのみが使用される
TEST_SOCIALACCOUNT_PROVIDERS = {
    'discord': {
        'SCOPE': ['identify', 'email'],
    }
}


class CommunityCreateFormTest(TestCase):
    """CommunityCreateFormのテスト."""

    def test_form_has_required_fields(self):
        """フォームに必須フィールドが含まれていることをテスト."""
        form = CommunityCreateForm()
        required_fields = [
            'name', 'start_time', 'duration', 'weekdays', 'frequency', 'organizers',
            'group_url', 'organizer_url', 'sns_url', 'discord', 'twitter_hashtag',
            'poster_image', 'allow_poster_repost', 'description', 'platform', 'tags',
            'guidelines_agreed',
        ]
        for field in required_fields:
            self.assertIn(field, form.fields)

    def test_poster_image_is_required(self):
        """poster_imageが必須であることをテスト."""
        form = CommunityCreateForm()
        self.assertTrue(form.fields['poster_image'].required)

    def test_tags_is_required(self):
        """tagsが必須であることをテスト."""
        form = CommunityCreateForm()
        self.assertTrue(form.fields['tags'].required)

    def test_guidelines_agreement_is_required_and_not_stored_on_model(self):
        """ガイドライン同意が必須の非モデルフィールドであることをテスト."""
        form = CommunityCreateForm()

        self.assertTrue(form.fields['guidelines_agreed'].required)
        self.assertFalse(bool(form.fields['guidelines_agreed'].initial))
        self.assertNotIn('guidelines_agreed', [field.name for field in Community._meta.fields])

    def test_tags_uses_form_tags_only(self):
        """tagsがFORM_TAGS（技術系・学術系）のみを使用していることをテスト."""
        from community.models import FORM_TAGS
        form = CommunityCreateForm()
        self.assertEqual(list(form.fields['tags'].choices), list(FORM_TAGS))

    def test_allow_poster_repost_default_is_true(self):
        """allow_poster_repostのデフォルト値がTrueであることをテスト."""
        form = CommunityCreateForm()
        self.assertTrue(form.fields['allow_poster_repost'].initial)

    def test_create_form_poster_help_text_includes_requirements(self):
        """作成フォームのposter_imageに推奨要件が表示されることをテスト."""
        form = CommunityCreateForm()
        self.assertIn('A4比率・縦4096px', form.fields['poster_image'].help_text)

    def test_update_form_poster_help_text_includes_requirements(self):
        """更新フォームのposter_imageに推奨要件が表示されることをテスト."""
        form = CommunityUpdateForm()
        self.assertIn('A4比率・縦4096px', form.fields['poster_image'].help_text)

    def test_create_form_x_labels_and_placeholders(self):
        """作成フォームのX向け表記が更新されていることをテスト."""
        form = CommunityCreateForm()

        self.assertEqual(form.fields['sns_url'].label, 'XアカウントURL')
        self.assertEqual(form.fields['twitter_hashtag'].label, 'Xハッシュタグ')
        self.assertEqual(form.fields['sns_url'].widget.attrs.get('placeholder'), 'https://x.com/XXXXX')
        self.assertEqual(form.fields['twitter_hashtag'].widget.attrs.get('placeholder'), '#VRChat')


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS)
class CommunityCreateViewTest(TestCase):
    """CommunityCreateViewのテスト."""

    def setUp(self):
        # Discord SocialAppを作成（テンプレートのprovider_login_urlタグに必要）
        # override_settingsでAPPS設定を無効化しているため、DBのSocialAppが使用される
        site = Site.objects.get_current()
        social_app = SocialApp.objects.create(
            provider='discord',
            name='Discord',
            client_id='test-client-id',
            secret='test-secret'
        )
        social_app.sites.add(site)

        self.client = Client()
        self.user = CustomUser.objects.create_user(
            user_name='テストユーザー',
            email='test@example.com',
            password='testpass123'
        )
        self.create_url = reverse('community:create')

    def _make_post_data(self, *, name='テスト集会名', organizer_url=''):
        """有効な集会登録データを生成する."""
        test_image = SimpleUploadedFile(
            name='test_poster.jpg',
            content=(
                b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x00\x00\x00\x21'
                b'\xf9\x04\x01\x0a\x00\x01\x00\x2c\x00\x00\x00\x00\x01\x00\x01'
                b'\x00\x00\x02\x02\x4c\x01\x00\x3b'
            ),
            content_type='image/gif',
        )
        return {
            'name': name,
            'start_time': '20:00',
            'duration': 60,
            'weekdays': ['Sat'],
            'frequency': '毎週',
            'organizers': 'テスト主催者',
            'group_url': '',
            'organizer_url': organizer_url,
            'sns_url': '',
            'discord': '',
            'twitter_hashtag': '',
            'poster_image': test_image,
            'allow_poster_repost': True,
            'description': 'テスト説明',
            'platform': 'All',
            'tags': ['tech'],
            'guidelines_agreed': True,
        }

    def test_unauthenticated_user_redirected_to_login(self):
        """未認証ユーザーがログインページにリダイレクトされることをテスト."""
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/account/login/', response.url)

    def test_authenticated_user_can_access_create_page(self):
        """認証済みユーザーが集会登録ページにアクセスできることをテスト."""
        self.client.force_login(self.user)
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'community/create.html')
        self.assertContains(response, 'A4比率・縦4096px')

    def test_submission_without_guidelines_agreement_is_rejected(self):
        """ガイドラインに同意しない送信では集会を作成しないことをテスト."""
        self.client.force_login(self.user)
        post_data = self._make_post_data(name='未同意集会')
        post_data.pop('guidelines_agreed')

        response = self.client.post(self.create_url, post_data)

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'],
            'guidelines_agreed',
            '登録するには、掲載・審査基準への同意が必要です。',
        )
        self.assertFalse(Community.objects.filter(name='未同意集会').exists())

    def test_user_with_existing_community_can_access_create_page(self):
        """既に集会を持っているユーザーも集会登録ページにアクセスできることをテスト（複数集会対応）."""
        # ユーザーに集会を作成
        existing_community = Community.objects.create(
            name='既存集会',
            frequency='毎週',
            organizers='テスト主催者',
            description='テスト説明'
        )
        CommunityMember.objects.create(
            community=existing_community,
            user=self.user,
            role=CommunityMember.Role.OWNER
        )
        self.client.force_login(self.user)
        response = self.client.get(self.create_url)
        # リダイレクトされずにページにアクセスできる
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'community/create.html')

    @patch('community.views.manage.requests.post')
    def test_new_community_starts_with_recording_disallowed_and_public(self, mock_discord_post):
        """登録した集会は撮影「許可しない」で始まり、撮影ステータスの初期値「公開」は保持する."""
        mock_discord_post.return_value = MagicMock(status_code=200)
        self.client.force_login(self.user)

        response = self.client.post(self.create_url, self._make_post_data(name='撮影初期値の集会'))

        self.assertEqual(response.status_code, 302)
        community = Community.objects.get(name='撮影初期値の集会')
        self.assertTrue(community.accepts_lt_application)
        self.assertFalse(community.recording_allowed)
        self.assertEqual(community.default_recording_policy, 'public')
        self.assertEqual(community.default_lt_duration, 30)
        self.assertEqual(community.lt_start_offset_minutes, 30)
        session = self.client.session
        session['active_community_id'] = community.pk
        session.save()
        pending_html = self.client.get(reverse('community:settings')).content.decode()
        self.assertIn('まだ公開されていません', pending_html)
        self.assertIn('id="recording_allowed_false"', pending_html)
        community.status = 'approved'
        community.save(update_fields=['status'])
        html = self.client.get(reverse('community:settings')).content.decode()
        self.assertRegex(html, r'id="recording_allowed_false"[^>]*checked')
        self.assertIn('id="default-recording-policy-section" style="display: none;"', html)
        self.assertRegex(html, r'id="default_recording_policy_public" name="default_recording_policy" value="public"\s+checked')
        self.assertNotRegex(html, r'id="default_recording_policy_allowed"[^>]*checked')

    def test_create_page_shows_reception_card_and_defers_complex_settings(self):
        """受付カードと承認後の案内を表示し、手間のかかる入力は出さない。"""
        self.client.force_login(self.user)
        response = self.client.get(self.create_url)
        self.assertContains(response, '発表の受付')
        self.assertContains(response, '承認後に設定できること')
        for name in (
            'accepts_lt_application', 'recording_allowed', 'default_recording_policy',
            'default_lt_duration', 'lt_start_offset_minutes',
        ):
            self.assertContains(response, f'name="{name}"')
        form = response.context['form']
        self.assertEqual(form['accepts_lt_application'].value(), 'on')
        self.assertEqual(form['recording_allowed'].value(), 'false')
        self.assertEqual(form['default_recording_policy'].value(), RecordingPolicy.PUBLIC)
        self.assertEqual(form['default_lt_duration'].value(), 30)
        self.assertEqual(form['lt_start_offset_minutes'].value(), 30)
        for label in (
            'Discord 通知', 'VRCイベントカレンダー', 'X の告知テンプレート',
            'メンバー', 'API キー', '発表申請の追加情報テンプレート',
        ):
            self.assertContains(response, label)
        for name in (
            'notification_webhook_url', 'discord_mention_user_ids', 'custom_calendar_description',
            'custom_calendar_join_method', 'custom_tweet_template', 'lt_application_template',
            'members', 'api_key',
        ):
            self.assertNotIn(name, form.fields)
            self.assertNotContains(response, f'name="{name}"')

    @patch('community.views.manage.requests.post')
    def test_selected_reception_settings_are_saved(self, mock_discord_post):
        """受付の有無・撮影・持ち時間・開始のずれを申請時に保存する。"""
        mock_discord_post.return_value = MagicMock(status_code=200)
        self.client.force_login(self.user)
        for accepts in ('on', 'off'):
            with self.subTest(accepts=accepts):
                data = self._make_post_data(name=f'受付設定-{accepts}')
                data.update({
                    'accepts_lt_application': accepts,
                    'recording_allowed': 'true',
                    'default_recording_policy': RecordingPolicy.ALLOWED,
                    'default_recording_policy_chosen': '1',
                    'default_lt_duration': '15',
                    'lt_start_offset_minutes': '0',
                    # 画面にない欄を送信されても申請では保存しない。
                    'lt_application_template': '申請では受け付けない',
                })
                response = self.client.post(self.create_url, data)
                self.assertEqual(response.status_code, 302)
                community = Community.objects.get(name=data['name'])
                self.assertEqual(community.accepts_lt_application, accepts == 'on')
                self.assertTrue(community.recording_allowed)
                self.assertEqual(community.default_recording_policy, RecordingPolicy.ALLOWED)
                self.assertEqual(community.default_lt_duration, 15)
                self.assertEqual(community.lt_start_offset_minutes, 0)
                self.assertEqual(community.lt_application_template, '')
                self.assertEqual(community.status, 'pending')

    @patch('community.views.manage.requests.post')
    def test_blank_duration_and_offset_use_existing_defaults(self, mock_discord_post):
        """数値欄を空にしても従来の30分を保存する。"""
        mock_discord_post.return_value = MagicMock(status_code=200)
        self.client.force_login(self.user)
        data = self._make_post_data()
        data.update(default_lt_duration='', lt_start_offset_minutes='')
        response = self.client.post(self.create_url, data)
        self.assertEqual(response.status_code, 302)
        community = Community.objects.get(name=data['name'])
        self.assertEqual(community.default_lt_duration, 30)
        self.assertEqual(community.lt_start_offset_minutes, 30)

    def test_invalid_duration_and_offset_are_rejected(self):
        """持ち時間0分や負の開始時刻を保存しない。"""
        self.client.force_login(self.user)
        data = self._make_post_data()
        data.update(default_lt_duration='0', lt_start_offset_minutes='-1')
        response = self.client.post(self.create_url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn('default_lt_duration', response.context['form'].errors)
        self.assertIn('lt_start_offset_minutes', response.context['form'].errors)
        self.assertFalse(Community.objects.filter(name=data['name']).exists())

    def test_too_large_duration_and_offset_are_rejected(self):
        """時刻計算があふれる大きな値を保存しない。"""
        self.client.force_login(self.user)
        data = self._make_post_data()
        data.update(default_lt_duration='241', lt_start_offset_minutes='4294967295')
        response = self.client.post(self.create_url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn('default_lt_duration', response.context['form'].errors)
        self.assertIn('lt_start_offset_minutes', response.context['form'].errors)
        self.assertFalse(Community.objects.filter(name=data['name']).exists())

    @patch('community.views.manage.requests.post')
    def test_recording_results_match_settings_save_for_same_input(self, mock_discord_post):
        """未送信・旧形式・不正値・明示選択でも設定画面と同じ撮影設定になる。"""
        mock_discord_post.return_value = MagicMock(status_code=200)
        self.client.force_login(self.user)
        cases = [
            ({}, False, RecordingPolicy.PUBLIC),
            ({'recording_allowed': 'true'}, True, RecordingPolicy.PUBLIC),
            ({'recording_allowed': 'on'}, True, RecordingPolicy.PUBLIC),
            ({'recording_allowed': 'invalid'}, False, RecordingPolicy.PUBLIC),
            ({'recording_allowed': ' true '}, False, RecordingPolicy.PUBLIC),
            ({'recording_allowed': 'true', 'default_recording_policy': 'invalid'}, True, RecordingPolicy.PUBLIC),
            ({'recording_allowed': 'true', 'default_recording_policy': ' public '}, True, RecordingPolicy.PUBLIC),
            ({'recording_allowed': 'false', 'default_recording_policy': 'forbidden'}, False, RecordingPolicy.FORBIDDEN),
        ]
        for policy in RecordingPolicy.values:
            for chosen in ('', '1'):
                cases.append((
                    {'recording_allowed': 'true', 'default_recording_policy': policy,
                     'default_recording_policy_chosen': chosen},
                    True,
                    RecordingPolicy.PUBLIC if policy == 'forbidden' and not chosen else policy,
                ))
        for index, (values, expected_allowed, expected_policy) in enumerate(cases):
            with self.subTest(values=values):
                data = self._make_post_data(name=f'撮影設定一致-{index}')
                data.update(values)
                response = self.client.post(self.create_url, data)
                self.assertEqual(response.status_code, 302)
                created = Community.objects.get(name=data['name'])
                existing = make_community(name=f'既存-{index}', owner=self.user)
                response = self.client.post(
                    reverse('community:update_lt_settings', kwargs={'pk': existing.pk}), values,
                )
                self.assertEqual(response.status_code, 302)
                existing.refresh_from_db()
                self.assertEqual(created.recording_allowed, expected_allowed)
                self.assertEqual(created.default_recording_policy, expected_policy)
                self.assertEqual(created.recording_allowed, existing.recording_allowed)
                self.assertEqual(created.default_recording_policy, existing.default_recording_policy)

    @patch('community.views.manage.requests.post')
    def test_community_creation_via_post(self, mock_discord_post):
        """POSTリクエストで集会が作成されることをテスト.

        nameフィールドを含めて送信し、集会が正常に作成されることを確認する。
        """
        # Discord通知のモック
        mock_discord_post.return_value = MagicMock(status_code=200)

        self.client.force_login(self.user)
        post_data = self._make_post_data(
            organizer_url='https://vrchat.com/home/user/usr_01b02b0e-58b5-4558-a6ca-56dd32dafdad',
        )

        response = self.client.post(self.create_url, post_data)

        # 成功時はsettingsページにリダイレクト
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('account:settings'))

        # 集会が作成されていることを確認
        self.assertTrue(Community.objects.filter(name='テスト集会名').exists())

        # 集会のnameが送信した値になっていることを確認
        created_community = Community.objects.get(name='テスト集会名')
        self.assertEqual(created_community.name, 'テスト集会名')
        self.user.refresh_from_db()
        self.assertEqual(
            self.user.vrchat_user_id,
            'usr_01b02b0e-58b5-4558-a6ca-56dd32dafdad',
        )

        # CommunityMemberが作成されていることを確認
        self.assertTrue(CommunityMember.objects.filter(
            community=created_community,
            user=self.user,
            role=CommunityMember.Role.OWNER
        ).exists())

    @patch('community.views.manage.requests.post')
    def test_community_create_creates_owner_membership(self, mock_discord_post):
        """集会作成時にオーナーとしてCommunityMemberが作成されることをテスト."""
        # Discord通知のモック
        mock_discord_post.return_value = MagicMock(status_code=200)

        self.client.force_login(self.user)
        post_data = self._make_post_data(name='オーナーテスト集会')

        response = self.client.post(self.create_url, post_data)

        # 成功時はsettingsページにリダイレクト
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('account:settings'))

        # 集会が作成されていることを確認
        community = Community.objects.get(name='オーナーテスト集会')

        # CommunityMemberが作成されていることを確認
        self.assertTrue(CommunityMember.objects.filter(
            community=community,
            user=self.user
        ).exists())

        # オーナー権限で作成されていることを確認
        membership = CommunityMember.objects.get(community=community, user=self.user)
        self.assertEqual(membership.role, CommunityMember.Role.OWNER)

        # is_ownerメソッドがTrueを返すことを確認
        self.assertTrue(community.is_owner(self.user))

        # can_editメソッドがTrueを返すことを確認
        self.assertTrue(community.can_edit(self.user))

    @patch('community.views.manage.requests.post')
    def test_user_with_existing_community_can_create_new_community(self, mock_discord_post):
        """既に集会を持っているユーザーが新しい集会を作成できることをテスト（複数集会対応）."""
        # Discord通知のモック
        mock_discord_post.return_value = MagicMock(status_code=200)

        # 既存の集会を作成
        existing_community = Community.objects.create(
            name='既存集会',
            frequency='毎週',
            organizers='テスト主催者',
            description='テスト説明'
        )
        CommunityMember.objects.create(
            community=existing_community,
            user=self.user,
            role=CommunityMember.Role.OWNER
        )

        self.client.force_login(self.user)
        post_data = self._make_post_data(name='新規集会')

        response = self.client.post(self.create_url, post_data)

        # 成功時はsettingsページにリダイレクト
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('account:settings'))

        # ユーザーが2つの集会のオーナーであることを確認
        user_memberships = CommunityMember.objects.filter(
            user=self.user,
            role=CommunityMember.Role.OWNER
        )
        self.assertEqual(user_memberships.count(), 2)

        # 新規集会が作成されていることを確認
        new_community = Community.objects.get(name='新規集会')

        # 新規集会のCommunityMemberが作成されていることを確認
        self.assertTrue(CommunityMember.objects.filter(
            community=new_community,
            user=self.user,
            role=CommunityMember.Role.OWNER
        ).exists())


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS)
class SettingsViewCommunityButtonTest(TestCase):
    """設定画面の集会登録ボタン表示テスト."""

    def setUp(self):
        # Discord SocialAppを作成（テンプレートのprovider_login_urlタグに必要）
        # override_settingsでAPPS設定を無効化しているため、DBのSocialAppが使用される
        site = Site.objects.get_current()
        social_app = SocialApp.objects.create(
            provider='discord',
            name='Discord',
            client_id='test-client-id',
            secret='test-secret'
        )
        social_app.sites.add(site)

        self.client = Client()
        self.user_with_community = CustomUser.objects.create_user(
            user_name='集会持ちユーザー',
            email='with@example.com',
            password='testpass123'
        )
        self.user_without_community = CustomUser.objects.create_user(
            user_name='集会なしユーザー',
            email='without@example.com',
            password='testpass123'
        )
        # 集会持ちユーザーに集会を作成
        community = Community.objects.create(
            name='テスト集会',
            frequency='毎週',
            organizers='テスト主催者',
            description='テスト説明',
            status='approved'
        )
        # CommunityMemberを作成
        CommunityMember.objects.create(
            community=community,
            user=self.user_with_community,
            role=CommunityMember.Role.OWNER
        )
        self.settings_url = reverse('account:settings')

    def test_user_without_community_sees_add_button(self):
        """集会がないユーザーに集会作成リンクが表示されることをテスト."""
        self.client.force_login(self.user_without_community)
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '新しい技術・学術系集会を始める')
        self.assertContains(response, reverse('community:create'))

    def test_user_with_community_also_sees_add_button(self):
        """集会があるユーザーにも集会作成リンクが表示されることをテスト（複数集会対応）."""
        self.client.force_login(self.user_with_community)
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 200)
        # 新しいレイアウトでは集会作成リンクは常に表示される
        self.assertContains(response, '新しい技術・学術系集会を始める')
        self.assertContains(response, reverse('community:create'))


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS)
class SettingsViewEmailAlertTest(TestCase):
    """設定画面のメールアドレス未設定アラート表示テスト."""

    def setUp(self):
        # Discord SocialAppを作成（テンプレートのprovider_login_urlタグに必要）
        # override_settingsでAPPS設定を無効化しているため、DBのSocialAppが使用される
        site = Site.objects.get_current()
        social_app = SocialApp.objects.create(
            provider='discord',
            name='Discord',
            client_id='test-client-id',
            secret='test-secret'
        )
        social_app.sites.add(site)

        self.client = Client()
        self.user_with_email = CustomUser.objects.create_user(
            user_name='メール有りユーザー',
            email='with@example.com',
            password='testpass123'
        )
        self.user_without_email = CustomUser.objects.create_user(
            user_name='メール無しユーザー',
            email='no-email-placeholder@example.com',
            password='testpass123'
        )
        # create_user は email 必須のため、移行前から空のまま残るユーザーを update で再現する
        CustomUser.objects.filter(pk=self.user_without_email.pk).update(email='')
        self.user_without_email.refresh_from_db()
        self.settings_url = reverse('account:settings')

    def test_user_without_email_sees_warning_alert(self):
        """メールアドレス未設定のユーザーに警告アラートが表示されることをテスト."""
        self.client.force_login(self.user_without_email)
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'メールアドレスが未設定です')

    def test_user_with_email_does_not_see_warning_alert(self):
        """メールアドレス設定済みのユーザーには警告アラートが表示されないことをテスト."""
        self.client.force_login(self.user_with_email)
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'メールアドレスが未設定です')
