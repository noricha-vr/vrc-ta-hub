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
        data = {'event': self.event.pk, 'theme': theme, 'speaker': '発表者'}
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

    def test_missing_value_falls_back_to_public(self, _mock_send):
        """選択が送られなかった時は既定の「公開」で保存される。"""
        detail = self._apply('未選択の発表')

        self.assertEqual(detail.recording_policy, RecordingPolicy.PUBLIC)

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
