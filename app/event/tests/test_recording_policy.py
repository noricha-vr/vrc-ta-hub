"""発表ごとの撮影の選択（EventDetail.recording_policy）のテスト。"""

from datetime import time
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
        self.community = make_community(name='申請先の集会')
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
        self.assertContains(response, '禁止（撮影しない）')
        self.assertRegex(
            response.content.decode(),
            r'<input type="radio" name="recording_policy" value="public"[^>]*checked',
        )

    def test_selected_value_is_saved(self, _mock_send):
        """「禁止」を選んで申請すると禁止で保存される。"""
        detail = self._apply('禁止の発表', recording_policy='forbidden')

        self.assertEqual(detail.recording_policy, RecordingPolicy.FORBIDDEN)

    def test_missing_value_is_saved_as_forbidden(self, _mock_send):
        """選択が送られなかった（撮影の選択肢を見ていない）時は「禁止」で保存される。"""
        detail = self._apply('未選択の発表')

        self.assertEqual(detail.recording_policy, RecordingPolicy.FORBIDDEN)

    def test_invalid_value_is_rejected(self, _mock_send):
        """選択肢にない値はフォームのエラーになる。"""
        form = LTApplicationForm(
            data={'event': self.event.pk, 'theme': 't', 'speaker': 's', 'recording_policy': 'secret'},
            community=self.community,
            user=self.user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('recording_policy', form.errors)

    def test_review_page_shows_policy(self, _mock_send):
        """主催者の審査画面に撮影の扱いが表示される。"""
        owner = make_discord_linked_user(user_name='review_owner', email='review_owner@example.com')
        community = make_community(name='審査の集会', owner=owner)
        detail = make_event_detail(
            make_event(community), applicant=self.user, recording_policy=RecordingPolicy.ALLOWED,
        )
        self.client.force_login(owner)

        response = self.client.get(reverse('event:lt_application_review', kwargs={'pk': detail.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '許可（撮影するが公開しない）')


class LTApplicationEditFormRecordingPolicyTest(TestCase):
    """発表者が自分の申請を編集するフォーム。"""

    def setUp(self):
        community = make_community(name='編集の集会')
        self.detail = make_event_detail(make_event(community), recording_policy=RecordingPolicy.PUBLIC)

    def _form(self, **extra):
        data = {'theme': 'テーマ', 'speaker': '発表者'}
        data.update(extra)
        return LTApplicationEditForm(data=data, instance=self.detail)

    def test_change_policy(self):
        """編集で「許可」に変えられる。"""
        form = self._form(recording_policy='allowed')

        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.recording_policy, RecordingPolicy.ALLOWED)

    def test_edit_page_shows_radios(self):
        """申請者の編集画面に撮影のラジオボタンが出る。"""
        applicant = make_discord_linked_user(user_name='edit_applicant', email='edit_applicant@example.com')
        self.detail.applicant = applicant
        self.detail.save(update_fields=['applicant'])
        self.client.force_login(applicant)

        response = self.client.get(reverse('account:lt_application_edit', kwargs={'pk': self.detail.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-field="recording_policy"')
        self.assertContains(response, '禁止（撮影しない）')

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
        self.community = make_community(name='主催の集会', owner=self.owner)
        self.event = make_event(self.community)
        self.client.force_login(self.owner)

    def _data(self, theme, **extra):
        data = {'detail_type': 'LT', 'theme': theme, 'speaker': '発表者', 'start_time': '22:30', 'duration': 30}
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

    def test_update_saves_selected_policy(self):
        """編集画面で値を変えられる。"""
        detail = make_event_detail(self.event, status='approved', start_time=time(22, 30))

        response = self.client.post(
            reverse('event:detail_update', kwargs={'pk': detail.pk}),
            self._data('主催編集', recording_policy='allowed'),
        )

        self.assertEqual(response.status_code, 302)
        detail.refresh_from_db()
        self.assertEqual(detail.recording_policy, RecordingPolicy.ALLOWED)

    def test_form_page_shows_radios(self):
        """登録画面に撮影のラジオボタンが出る。"""
        response = self.client.get(reverse('event:detail_create', kwargs={'event_pk': self.event.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-field="recording_policy"')
        self.assertContains(response, '公開（撮影して YouTube で公開）')


@patch('event.notifications.send_mail', return_value=1)
class LTApplicationCommunityDefaultTest(TestCase):
    """申請フォームの撮影の初期値は、集会の「撮影ステータスの初期値」になる。"""

    def setUp(self):
        self.user = make_discord_linked_user(user_name='default_applicant', email='default_applicant@example.com')
        self.community = make_community(name='既定値を変えた集会')
        self.community.default_recording_policy = RecordingPolicy.ALLOWED
        self.community.save(update_fields=['default_recording_policy'])
        self.event = make_event(self.community)
        self.url = reverse('event:lt_application_create', kwargs={'community_pk': self.community.pk})
        self.client.force_login(self.user)

    def test_form_initial_is_community_default(self, _mock_send):
        """集会の撮影ステータスの初期値が「許可」なら、申請フォームは「許可」が選択済み。"""
        html = self.client.get(self.url).content.decode()

        self.assertRegex(html, r'<input type="radio" name="recording_policy" value="allowed"[^>]*checked')
        self.assertNotRegex(html, r'<input type="radio" name="recording_policy" value="public"[^>]*checked')

    def test_each_community_default_becomes_initial(self, _mock_send):
        """3 つのどの値も、そのまま申請フォームの初期値になる。"""
        for value in RecordingPolicy.values:
            with self.subTest(value=value):
                self.community.default_recording_policy = value
                form = LTApplicationForm(community=self.community, user=self.user)

                self.assertEqual(form['recording_policy'].value(), value)

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

    def test_owner_can_still_change_policy(self):
        """主催者は従来どおり選択肢を見て変えられる。"""
        self.client.force_login(self.owner)

        response = self._post('allowed')

        self.assertEqual(response.status_code, 302)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.recording_policy, RecordingPolicy.ALLOWED)

    def test_applicant_can_change_policy_when_allowed(self):
        """撮影を許可する集会なら、発表者も従来どおり選択肢を見て変えられる。"""
        community = self.detail.event.community
        community.recording_allowed = True
        community.save(update_fields=['recording_allowed'])
        self.client.force_login(self.applicant)

        self.assertContains(self.client.get(self.url), 'name="recording_policy"')
        response = self._post('allowed')

        self.assertEqual(response.status_code, 302)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.recording_policy, RecordingPolicy.ALLOWED)
