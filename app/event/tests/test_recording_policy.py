"""発表ごとの撮影の選択（EventDetail.recording_policy）のテスト。"""

from datetime import time
import re
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from event.forms import EventDetailForm, LTApplicationEditForm, LTApplicationForm
from event.models import EventDetail
from tests.factories import (
    make_community,
    make_discord_linked_user,
    make_event,
    make_event_detail,
)

RecordingPolicy = EventDetail.RecordingPolicy


def assert_recording_policy_html(test_case, response):
    """各フォームの表示順と、固定ヘルプのリンクが実際の HTML になること。"""
    html = response.content.decode()
    test_case.assertEqual(
        re.findall(r'<input type="radio" name="recording_policy" value="([^"]+)"', html),
        ['public', 'forbidden'],
    )
    for label in (
        '許可する（ボットで撮影して YouTube で公開）',
        '許可しない（ボットで撮影しない）',
    ):
        test_case.assertContains(response, label)
    test_case.assertNotContains(response, '許可（撮影するが公開しない）')
    test_case.assertContains(
        response,
        'ハブの撮影ボットによる録画・YouTube 公開の可否です'
        '（集会独自の撮影や参加者の写真撮影を制限する設定ではありません）。'
        '公開先は <a href="https://www.youtube.com/channel/UCNISeQgk2_lDF_4_UGWZ_Fw" '
        'target="_blank" rel="noopener">Hub の YouTube チャンネル</a> です。'
        '<a href="/news/2026-10-11-auto-recording-beta/" target="_blank" rel="noopener">撮影について詳しく</a>',
    )
    test_case.assertNotContains(response, '&lt;a href=')


class RecordingPolicyModelTest(TestCase):
    """モデルの選択肢と既定値。"""

    def test_default_is_public(self):
        """発表は既定で「公開」になる。"""
        community = make_community(name='既定値の集会')
        detail = make_event_detail(make_event(community))

        detail.refresh_from_db()
        self.assertEqual(detail.recording_policy, RecordingPolicy.PUBLIC)

    def test_choices_and_labels(self):
        """API の値と画面の表示名が仕様どおり。"""
        self.assertEqual(
            list(RecordingPolicy.choices),
            [
                ('forbidden', '禁止（撮影しない）'),
                ('allowed', '許可（撮影するが公開しない）'),
                ('public', '公開（撮影して YouTube で公開）'),
            ],
        )


@patch('event.notifications.send_mail', return_value=1)
class LTApplicationRecordingPolicyTest(TestCase):
    """発表者の申請フォームで選んだ撮影の扱いが保存される。"""

    def setUp(self):
        self.user = make_discord_linked_user(user_name='applicant', email='applicant@example.com')
        self.community = make_community(name='申請先の集会', recording_allowed=True)
        self.event = make_event(self.community)
        self.url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})

    def _apply(self, theme, **extra):
        data = {'event': self.event.pk, 'theme': theme, 'speaker': '発表者', 'article_consent': 'ok'}
        data.update(extra)
        self.client.force_login(self.user)
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        return EventDetail.objects.get(event=self.event, theme=theme)

    def test_form_shows_public_selected(self, _mock_send):
        """申請フォームは「公開」が選択済みで表示される。"""
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '許可しない（ボットで撮影しない）')
        assert_recording_policy_html(self, response)
        self.assertRegex(
            response.content.decode(),
            r'<input type="radio" name="recording_policy" value="public"[^>]*checked',
        )

    def test_selected_value_is_saved(self, _mock_send):
        """「許可しない」を選んで申請すると forbidden で保存される。"""
        detail = self._apply('禁止の発表', recording_policy='forbidden')

        self.assertEqual(detail.recording_policy, RecordingPolicy.FORBIDDEN)

    def test_missing_value_is_saved_as_forbidden(self, _mock_send):
        """選択が送られなかった（撮影の選択肢を見ていない）時は「禁止」で保存される。"""
        detail = self._apply('未選択の発表')

        self.assertEqual(detail.recording_policy, RecordingPolicy.FORBIDDEN)

    def test_invalid_value_is_rejected(self, _mock_send):
        """選択肢にない値（旧 allowed を含む）はフォームのエラーになる。"""
        for bad_value in ('secret', 'allowed'):
            with self.subTest(bad_value=bad_value):
                form = LTApplicationForm(
                    data={'event': self.event.pk, 'theme': 't', 'speaker': 's', 'recording_policy': bad_value},
                    community=self.community,
                    user=self.user,
                )

                self.assertFalse(form.is_valid())
                self.assertIn('recording_policy', form.errors)

    def test_review_page_shows_policy(self, _mock_send):
        """主催者の審査画面にボット撮影のラベル（旧 allowed を含む）が表示される。"""
        owner = make_discord_linked_user(user_name='review_owner', email='review_owner@example.com')
        community = make_community(name='審査の集会', owner=owner)
        review_event = make_event(community)
        self.client.force_login(owner)
        cases = (
            (RecordingPolicy.PUBLIC, '許可する（ボットで撮影して YouTube で公開）'),
            (RecordingPolicy.FORBIDDEN, '許可しない（ボットで撮影しない）'),
            (RecordingPolicy.ALLOWED, '旧設定（撮影するが公開しない）'),
        )
        for policy, expected_label in cases:
            with self.subTest(policy=policy):
                detail = make_event_detail(
                    review_event, applicant=self.user, recording_policy=policy,
                )
                response = self.client.get(reverse('event:lt_application_review', kwargs={'pk': detail.pk}))

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'ボットによる自動撮影')
                self.assertContains(response, expected_label)


class LTApplicationEditFormRecordingPolicyTest(TestCase):
    """発表者が自分の申請を編集するフォーム。"""

    def setUp(self):
        community = make_community(name='編集の集会', recording_allowed=True)
        self.detail = make_event_detail(make_event(community), recording_policy=RecordingPolicy.PUBLIC)

    def _form(self, **extra):
        data = {'theme': 'テーマ', 'speaker': '発表者'}
        data.update(extra)
        return LTApplicationEditForm(data=data, instance=self.detail)

    def test_change_policy(self):
        """編集で「許可しない」に変えられ、旧「allowed」は新規選択できない。"""
        bad = self._form(recording_policy='allowed')
        self.assertFalse(bad.is_valid())
        self.assertIn('recording_policy', bad.errors)

        form = self._form(recording_policy='forbidden')

        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.recording_policy, RecordingPolicy.FORBIDDEN)

    def test_legacy_allowed_instance_preserves_unless_changed(self):
        """旧設定「allowed」の既存申請は、未選択で保存すれば維持され、変更すれば更新される。"""
        self.detail.recording_policy = RecordingPolicy.ALLOWED
        self.detail.save(update_fields=['recording_policy'])

        kept = self._form()
        self.assertTrue(kept.is_valid(), kept.errors)
        kept.save()
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.recording_policy, RecordingPolicy.ALLOWED)

        updated = self._form(recording_policy='public')
        self.assertTrue(updated.is_valid(), updated.errors)
        updated.save()
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.recording_policy, RecordingPolicy.PUBLIC)

    def test_edit_page_shows_radios(self):
        """申請者の編集画面に撮影のラジオボタンが出る。"""
        applicant = make_discord_linked_user(user_name='edit_applicant', email='edit_applicant@example.com')
        self.detail.applicant = applicant
        self.detail.save(update_fields=['applicant'])
        self.client.force_login(applicant)

        response = self.client.get(reverse('account:lt_application_edit', kwargs={'pk': self.detail.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-field="recording_policy"')
        self.assertContains(response, '許可しない（ボットで撮影しない）')
        assert_recording_policy_html(self, response)

    def test_edit_page_shows_legacy_notice_only_for_allowed(self):
        """申請者の編集画面では、旧設定 allowed の時だけ現在値の案内を出す。"""
        applicant = make_discord_linked_user(user_name='legacy_notice_app', email='legacy_notice_app@example.com')
        self.detail.applicant = applicant
        self.detail.save(update_fields=['applicant'])
        self.client.force_login(applicant)
        url = reverse('account:lt_application_edit', kwargs={'pk': self.detail.pk})
        notice = '現在は「旧設定（撮影するが公開しない）」が設定されています。'

        for policy, expected in (
            (RecordingPolicy.PUBLIC, False),
            (RecordingPolicy.FORBIDDEN, False),
            (RecordingPolicy.ALLOWED, True),
        ):
            with self.subTest(policy=policy):
                self.detail.recording_policy = policy
                self.detail.save(update_fields=['recording_policy'])
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                if expected:
                    self.assertContains(response, notice)
                else:
                    self.assertNotContains(response, notice)
                assert_recording_policy_html(self, response)

    def test_missing_value_keeps_current_policy(self):
        """選択が送られなかった時は今の値を変えない。"""
        self.detail.recording_policy = RecordingPolicy.FORBIDDEN
        self.detail.save(update_fields=['recording_policy'])
        form = self._form()

        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.recording_policy, RecordingPolicy.FORBIDDEN)


class EventDetailFormRecordingPolicyTest(TestCase):
    """主催者が発表を直接登録・編集する画面。"""

    def setUp(self):
        self.owner = make_discord_linked_user(user_name='detail_owner', email='detail_owner@example.com')
        self.community = make_community(name='主催の集会', owner=self.owner, recording_allowed=True)
        self.event = make_event(self.community)
        self.client.force_login(self.owner)

    def _data(self, theme, **extra):
        data = {'detail_type': 'LT', 'theme': theme, 'speaker': '発表者', 'start_time': '22:30', 'duration': 30, 'article_consent': 'ok'}
        data.update(extra)
        return data

    def test_new_form_has_public_selected(self):
        """新規登録フォームは「公開」が初期選択。"""
        form = EventDetailForm()

        self.assertEqual(form['recording_policy'].value(), RecordingPolicy.PUBLIC)

    def test_create_saves_selected_policy(self):
        """登録画面で選んだ値が保存される。"""
        response = self.client.post(
            reverse('event:detail_create', kwargs={'event_pk': self.event.pk}),
            self._data('主催登録', recording_policy='forbidden'),
        )

        self.assertEqual(response.status_code, 302)
        detail = EventDetail.objects.get(event=self.event, theme='主催登録')
        self.assertEqual(detail.recording_policy, RecordingPolicy.FORBIDDEN)

    def test_create_without_policy_is_forbidden(self):
        """撮影の選択肢が届かない新規登録（表示後に集会が撮影を許可した等）は「公開」にしない。"""
        response = self.client.post(
            reverse('event:detail_create', kwargs={'event_pk': self.event.pk}),
            self._data('未送信の登録'),
        )

        self.assertEqual(response.status_code, 302)
        detail = EventDetail.objects.get(event=self.event, theme='未送信の登録')
        self.assertEqual(detail.recording_policy, RecordingPolicy.FORBIDDEN)

    def test_update_saves_selected_policy(self):
        """編集画面で値を変えられ、旧「allowed」は拒否される（既存は未選択のまま保持）。"""
        detail = make_event_detail(self.event, status='approved', start_time=time(22, 30))

        rejected = self.client.post(
            reverse('event:detail_update', kwargs={'pk': detail.pk}),
            self._data('主催編集', recording_policy='allowed'),
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertIn('recording_policy', rejected.context['form'].errors)
        detail.refresh_from_db()
        self.assertEqual(detail.recording_policy, RecordingPolicy.PUBLIC)

        response = self.client.post(
            reverse('event:detail_update', kwargs={'pk': detail.pk}),
            self._data('主催編集', recording_policy='forbidden'),
        )

        self.assertEqual(response.status_code, 302)
        detail.refresh_from_db()
        self.assertEqual(detail.recording_policy, RecordingPolicy.FORBIDDEN)

        legacy = make_event_detail(
            self.event, status='approved', start_time=time(23, 0),
            recording_policy=RecordingPolicy.ALLOWED,
        )
        kept = self.client.post(
            reverse('event:detail_update', kwargs={'pk': legacy.pk}),
            self._data('旧設定維持'),
        )
        self.assertEqual(kept.status_code, 302)
        legacy.refresh_from_db()
        self.assertEqual(legacy.recording_policy, RecordingPolicy.ALLOWED)

    def test_form_page_shows_radios(self):
        """登録画面に撮影のラジオボタンが出る。"""
        response = self.client.get(reverse('event:detail_create', kwargs={'event_pk': self.event.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-field="recording_policy"')
        self.assertContains(response, '許可する（ボットで撮影して YouTube で公開）')
        assert_recording_policy_html(self, response)

    def test_new_form_uses_each_community_default(self):
        """ボット撮影を許可する集会の初期値（旧 allowed は forbidden 扱い）を新規登録画面に反映する。"""
        for saved, expected in (
            (RecordingPolicy.PUBLIC, RecordingPolicy.PUBLIC),
            (RecordingPolicy.FORBIDDEN, RecordingPolicy.FORBIDDEN),
            (RecordingPolicy.ALLOWED, RecordingPolicy.FORBIDDEN),
        ):
            with self.subTest(saved=saved, expected=expected):
                self.community.default_recording_policy = saved
                self.community.save(update_fields=['default_recording_policy'])

                response = self.client.get(reverse('event:detail_create', kwargs={'event_pk': self.event.pk}))

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['form']['recording_policy'].value(), expected)
                self.assertRegex(
                    response.content.decode(),
                    rf'<input type="radio" name="recording_policy" value="{expected}"[^>]*checked',
                )
                self.assertNotContains(response, 'この集会はハブの自動撮影ボットによる撮影を許可していません。')

    def test_edit_form_keeps_saved_policy_instead_of_community_default(self):
        """編集画面では集会の初期値で既存の選択を上書きしない。"""
        detail = make_event_detail(self.event, recording_policy=RecordingPolicy.ALLOWED)

        response = self.client.get(reverse('event:detail_update', kwargs={'pk': detail.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form']['recording_policy'].value(), RecordingPolicy.ALLOWED)
        assert_recording_policy_html(self, response)

    def test_edit_page_shows_legacy_notice_only_for_allowed(self):
        """主催者の編集画面では、旧設定 allowed の時だけ現在値の案内を出す。"""
        notice = '現在は「旧設定（撮影するが公開しない）」が設定されています。'
        for policy, expected in (
            (RecordingPolicy.PUBLIC, False),
            (RecordingPolicy.FORBIDDEN, False),
            (RecordingPolicy.ALLOWED, True),
        ):
            with self.subTest(policy=policy):
                detail = make_event_detail(self.event, recording_policy=policy)
                response = self.client.get(reverse('event:detail_update', kwargs={'pk': detail.pk}))
                self.assertEqual(response.status_code, 200)
                if expected:
                    self.assertContains(response, notice)
                else:
                    self.assertNotContains(response, notice)
                assert_recording_policy_html(self, response)


class OrganizerRecordingNotAllowedTest(TestCase):
    """撮影を許可しない集会では主催者にも選択肢を出さない。"""

    def setUp(self):
        self.owner = make_discord_linked_user(user_name='ng_owner', email='ng_owner@example.com')
        self.community = make_community(
            name='撮影不可の主催集会', owner=self.owner, recording_allowed=False,
            default_recording_policy=RecordingPolicy.PUBLIC,
        )
        self.event = make_event(self.community)
        self.create_url = reverse('event:detail_create', kwargs={'event_pk': self.event.pk})
        self.client.force_login(self.owner)

    def _data(self, **extra):
        return {
            'detail_type': 'LT', 'theme': '主催者の発表', 'speaker': '発表者',
            'start_time': '22:30', 'duration': 30, 'article_consent': 'ok', **extra,
        }

    def test_create_and_edit_pages_hide_choices_and_link_to_settings(self):
        detail = make_event_detail(self.event, recording_policy=RecordingPolicy.PUBLIC)
        for url in (self.create_url, reverse('event:detail_update', kwargs={'pk': detail.pk})):
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertNotIn('recording_policy', response.context['form'].fields)
                self.assertNotContains(response, 'name="recording_policy"')
                self.assertContains(response, 'この集会はハブの自動撮影ボットによる撮影を許可していません。')
                self.assertContains(
                    response,
                    f'<a href="{reverse("community:settings")}">集会設定</a>',
                    html=True,
                )
                self.assertContains(response, 'で許可すると選べます')

    def test_create_saves_forbidden_even_with_submitted_policy(self):
        for extra in ({}, {'recording_policy': 'public'}, {'recording_policy': 'invalid'}):
            with self.subTest(extra=extra):
                response = self.client.post(self.create_url, self._data(**extra))

                self.assertEqual(response.status_code, 302)
                detail = EventDetail.objects.filter(event=self.event).latest('pk')
                self.assertEqual(detail.recording_policy, RecordingPolicy.FORBIDDEN)

    def test_edit_preserves_each_saved_policy_even_with_submitted_policy(self):
        for value in RecordingPolicy.values:
            for extra in ({}, {'recording_policy': 'public'}, {'recording_policy': 'invalid'}):
                with self.subTest(value=value, extra=extra):
                    detail = make_event_detail(self.event, recording_policy=value)

                    response = self.client.post(
                        reverse('event:detail_update', kwargs={'pk': detail.pk}), self._data(**extra),
                    )

                    self.assertEqual(response.status_code, 302)
                    detail.refresh_from_db()
                    self.assertEqual(detail.theme, '主催者の発表')
                    self.assertEqual(detail.recording_policy, value)


@patch('event.notifications.send_mail', return_value=1)
class LTApplicationCommunityDefaultTest(TestCase):
    """申請フォームの撮影の初期値は、集会の「ボット撮影許可の初期値」になる。"""

    def setUp(self):
        self.user = make_discord_linked_user(user_name='default_applicant', email='default_applicant@example.com')
        self.community = make_community(name='既定値を変えた集会', recording_allowed=True)
        self.community.default_recording_policy = RecordingPolicy.FORBIDDEN
        self.community.save(update_fields=['default_recording_policy'])
        self.event = make_event(self.community)
        self.url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})
        self.client.force_login(self.user)

    def test_form_initial_is_community_default(self, _mock_send):
        """集会のボット撮影許可の初期値が「許可しない」なら、申請フォームも「許可しない」が選択済み。"""
        html = self.client.get(self.url).content.decode()

        self.assertRegex(html, r'<input type="radio" name="recording_policy" value="forbidden"[^>]*checked')
        self.assertNotRegex(html, r'<input type="radio" name="recording_policy" value="public"[^>]*checked')

    def test_each_community_default_becomes_initial(self, _mock_send):
        """有効な初期値 2 つはそのまま反映され、旧「allowed」は安全側で forbidden になる。"""
        for saved, expected in (
            (RecordingPolicy.PUBLIC, RecordingPolicy.PUBLIC),
            (RecordingPolicy.FORBIDDEN, RecordingPolicy.FORBIDDEN),
            (RecordingPolicy.ALLOWED, RecordingPolicy.FORBIDDEN),
        ):
            with self.subTest(saved=saved, expected=expected):
                self.community.default_recording_policy = saved
                form = LTApplicationForm(community=self.community, user=self.user)

                self.assertEqual(form['recording_policy'].value(), expected)

    def test_missing_value_is_saved_as_forbidden(self, _mock_send):
        """選択が送られなかった（撮影の選択肢を見ていない）時は、集会の初期値ではなく「禁止」で保存される。"""
        self.client.post(self.url, {'event': self.event.pk, 'theme': '未選択', 'speaker': '発表者', 'article_consent': 'ok'})

        detail = EventDetail.objects.get(event=self.event, theme='未選択')
        self.assertEqual(detail.recording_policy, RecordingPolicy.FORBIDDEN)

    def test_form_opened_before_recording_was_allowed_is_saved_as_forbidden(self, _mock_send):
        """撮影を許可しない間に開いたフォームを、主催者が許可した後に送っても「公開」にならない。"""
        self.community.recording_allowed = False
        self.community.save(update_fields=['recording_allowed'])
        self.assertNotContains(self.client.get(self.url), 'name="recording_policy"')
        self.community.recording_allowed = True
        self.community.default_recording_policy = RecordingPolicy.PUBLIC
        self.community.save(update_fields=['recording_allowed', 'default_recording_policy'])

        self.client.post(self.url, {'event': self.event.pk, 'theme': '許可前に開いた', 'speaker': '発表者', 'article_consent': 'ok'})

        detail = EventDetail.objects.get(event=self.event, theme='許可前に開いた')
        self.assertEqual(detail.recording_policy, RecordingPolicy.FORBIDDEN)

    def test_speaker_can_change_from_default(self, _mock_send):
        """初期値と違う値を選べばその値で保存される。"""
        self.client.post(self.url, {
            'event': self.event.pk, 'theme': '変更', 'speaker': '発表者',
            'recording_policy': 'public', 'article_consent': 'ok',
        })

        detail = EventDetail.objects.get(event=self.event, theme='変更')
        self.assertEqual(detail.recording_policy, RecordingPolicy.PUBLIC)


@patch('event.notifications.send_mail', return_value=1)
class LTApplicationRecordingNotAllowedTest(TestCase):
    """撮影を「許可しない」集会への申請は、選択肢を出さず「禁止」で受け付ける。"""

    def setUp(self):
        self.user = make_discord_linked_user(user_name='ng_applicant', email='ng_applicant@example.com')
        self.community = make_community(name='撮影しない集会')
        self.community.recording_allowed = False
        # デフォルトが「公開」のままでも、許可しない方が優先される
        self.community.default_recording_policy = RecordingPolicy.PUBLIC
        self.community.save(update_fields=['recording_allowed', 'default_recording_policy'])
        self.event = make_event(self.community)
        self.url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})
        self.client.force_login(self.user)

    def test_form_hides_choices(self, _mock_send):
        """申請フォームには撮影の項目を見出しごと出さない。"""
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('recording_policy', response.context['form'].fields)
        self.assertNotContains(response, 'name="recording_policy"')
        self.assertNotContains(response, '撮影されません')
        self.assertNotContains(response, 'bi-camera-video')

    def test_submitted_value_is_ignored_and_saved_as_forbidden(self, _mock_send):
        """「公開」を送っても「禁止」で保存される。"""
        response = self.client.post(self.url, {
            'event': self.event.pk, 'theme': '送信', 'speaker': '発表者',
            'recording_policy': 'public', 'article_consent': 'ok',
        })

        self.assertEqual(response.status_code, 302)
        detail = EventDetail.objects.get(event=self.event, theme='送信')
        self.assertEqual(detail.recording_policy, RecordingPolicy.FORBIDDEN)

    def test_without_value_is_saved_as_forbidden(self, _mock_send):
        """何も送らなくても「禁止」で保存される。"""
        self.client.post(self.url, {'event': self.event.pk, 'theme': '未送信', 'speaker': '発表者', 'article_consent': 'ok'})

        detail = EventDetail.objects.get(event=self.event, theme='未送信')
        self.assertEqual(detail.recording_policy, RecordingPolicy.FORBIDDEN)


class LTApplicationEditRecordingNotAllowedTest(TestCase):
    """撮影を「許可しない」集会の申請を、発表者が編集する時。"""

    def setUp(self):
        self.applicant = make_discord_linked_user(user_name='ng_editor', email='ng_editor@example.com')
        community = make_community(name='撮影しない集会（編集）')
        community.recording_allowed = False
        community.save(update_fields=['recording_allowed'])
        self.detail = make_event_detail(
            make_event(community), applicant=self.applicant, recording_policy=RecordingPolicy.ALLOWED,
        )

    def test_form_has_no_choice_and_keeps_current_value(self):
        """選択肢を出さず、送られた値があっても今の値を書き換えない。"""
        form = LTApplicationEditForm(
            data={'theme': 'テーマ', 'speaker': '発表者', 'recording_policy': 'public'}, instance=self.detail,
        )

        self.assertNotIn('recording_policy', form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.recording_policy, RecordingPolicy.ALLOWED)

    def test_edit_page_has_no_recording_section(self):
        """編集画面には撮影の項目を見出しごと出さない。"""
        self.client.force_login(self.applicant)

        response = self.client.get(reverse('account:lt_application_edit', kwargs={'pk': self.detail.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-field="recording_policy"')
        self.assertNotContains(response, '撮影されません')
        self.assertNotContains(response, 'bi-camera-video')


class EventDetailUpdateRecordingNotAllowedTest(TestCase):
    """撮影を「許可しない」集会の承認済み発表を、発表者が発表詳細の編集画面で直す時。"""

    def setUp(self):
        self.owner = make_discord_linked_user(user_name='ng_detail_owner', email='ng_detail_owner@example.com')
        self.applicant = make_discord_linked_user(user_name='ng_detail_speaker', email='ng_detail_speaker@example.com')
        community = make_community(name='撮影しない集会（発表詳細）', owner=self.owner)
        community.recording_allowed = False
        community.save(update_fields=['recording_allowed'])
        self.detail = make_event_detail(
            make_event(community), applicant=self.applicant, status='approved',
            start_time=time(22, 30), recording_policy=RecordingPolicy.FORBIDDEN,
        )
        self.url = reverse('event:detail_update', kwargs={'pk': self.detail.pk})

    def _post(self, recording_policy):
        return self.client.post(self.url, {
            'detail_type': 'LT', 'theme': '発表者の編集', 'speaker': '発表者', 'start_time': '22:30',
            'duration': 30, 'recording_policy': recording_policy,
        })

    def test_applicant_sees_no_recording_section(self):
        """発表者には撮影の項目を見出しごと出さない。"""
        self.client.force_login(self.applicant)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('recording_policy', response.context['form'].fields)
        self.assertNotContains(response, 'name="recording_policy"')
        self.assertNotContains(response, '撮影されません')
        self.assertNotContains(response, 'bi-camera-video')

    def test_applicant_cannot_change_policy(self):
        """発表者が「公開」を送っても今の値（禁止）のまま。"""
        self.client.force_login(self.applicant)

        response = self._post('public')

        self.assertEqual(response.status_code, 302)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.theme, '発表者の編集')
        self.assertEqual(self.detail.recording_policy, RecordingPolicy.FORBIDDEN)

    def test_owner_cannot_change_policy_when_not_allowed(self):
        """主催者が値を送っても撮影不可なら今の値を保つ。"""
        self.client.force_login(self.owner)

        response = self._post('allowed')

        self.assertEqual(response.status_code, 302)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.recording_policy, RecordingPolicy.FORBIDDEN)

    def test_applicant_can_change_policy_when_allowed(self):
        """ボット撮影を許可する集会なら、発表者も選択肢を見て変えられ、旧 allowed は選べない。"""
        community = self.detail.event.community
        community.recording_allowed = True
        community.save(update_fields=['recording_allowed'])
        self.client.force_login(self.applicant)

        self.assertContains(self.client.get(self.url), 'name="recording_policy"')
        rejected = self._post('allowed')
        self.assertEqual(rejected.status_code, 200)
        self.assertIn('recording_policy', rejected.context['form'].errors)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.recording_policy, RecordingPolicy.FORBIDDEN)

        response = self._post('public')

        self.assertEqual(response.status_code, 302)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.recording_policy, RecordingPolicy.PUBLIC)


class EventDetailAdminFormRecordingPolicyTest(TestCase):
    """Django 管理画面の EventDetailAdminForm でも新規 allowed 選択を禁止し、既存 allowed のみ保持する。"""

    def setUp(self):
        self.event = make_event(make_community(name='管理画面発表の集会'))

    def _form(self, instance=None, **extra):
        from event.admin import EventDetailAdminForm

        data = {
            'event': self.event.pk,
            'detail_type': 'LT',
            'start_time': '22:00',
            'duration': 30,
            'recording_policy': 'public',
            'article_consent': 'unanswered',
            'status': 'approved',
            'theme': '管理画面の発表',
            'speaker': '発表者',
            'slide_fetch_attempts': 0,
            'article_generation_attempts': 0,
            'article_generation_deferrals': 0,
        }
        data.update(extra)
        return EventDetailAdminForm(instance=instance, data=data)

    def test_new_and_current_forms_offer_two_choices_and_reject_allowed(self):
        from event.admin import EventDetailAdminForm

        new_form = EventDetailAdminForm()
        self.assertEqual(
            list(new_form.fields['recording_policy'].choices),
            [
                ('public', '許可する（ボットで撮影して YouTube で公開）'),
                ('forbidden', '許可しない（ボットで撮影しない）'),
            ],
        )

        rejected_create = self._form(recording_policy='allowed')
        self.assertFalse(rejected_create.is_valid())
        self.assertIn('recording_policy', rejected_create.errors)

        current_detail = make_event_detail(self.event, detail_type='LT', recording_policy=RecordingPolicy.PUBLIC)
        rejected_update = self._form(current_detail, recording_policy='allowed')
        self.assertFalse(rejected_update.is_valid())
        self.assertIn('recording_policy', rejected_update.errors)

    def test_existing_allowed_lt_keeps_choice_and_allows_preserve_or_update(self):
        from event.admin import EventDetailAdminForm

        legacy = make_event_detail(self.event, detail_type='LT', recording_policy=RecordingPolicy.ALLOWED)
        legacy_form = EventDetailAdminForm(instance=legacy)
        self.assertEqual(
            list(legacy_form.fields['recording_policy'].choices),
            [
                ('public', '許可する（ボットで撮影して YouTube で公開）'),
                ('forbidden', '許可しない（ボットで撮影しない）'),
                ('allowed', '旧設定（撮影するが公開しない）'),
            ],
        )

        kept = self._form(legacy, recording_policy='allowed', theme='テーマ微調整')
        self.assertTrue(kept.is_valid(), kept.errors)
        kept.save()
        legacy.refresh_from_db()
        self.assertEqual(legacy.recording_policy, RecordingPolicy.ALLOWED)
        self.assertEqual(legacy.theme, 'テーマ微調整')

        updated = self._form(legacy, recording_policy='public')
        self.assertTrue(updated.is_valid(), updated.errors)
        updated.save()
        legacy.refresh_from_db()
        self.assertEqual(legacy.recording_policy, RecordingPolicy.PUBLIC)
