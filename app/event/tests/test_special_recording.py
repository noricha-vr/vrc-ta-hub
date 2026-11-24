"""特別企画の撮影設定は、画面で毎回出演者全員への確認を求める。"""
from unittest.mock import patch

from django.contrib import admin
from django.test import RequestFactory, TestCase
from django.urls import reverse

from event.admin import EventDetailAdmin, EventDetailAdminForm, EventDetailInline
from event.forms import EventDetailForm
from event.models import Event, EventDetail
from tests.factories import (
    make_community, make_discord_linked_user, make_event, make_event_detail, make_user,
)


class SpecialRecordingFormTest(TestCase):
    def setUp(self):
        self.owner = make_user()
        self.community = make_community(owner=self.owner, recording_allowed=True)
        self.event = make_event(self.community)
        self.request = RequestFactory().get('/')
        self.request.user = self.owner

    def form(self, *, instance=None, **extra):
        return EventDetailForm(
            data={
                'detail_type': 'SPECIAL', 'start_time': '22:00', 'duration': 95,
                'h1': '  パネルの企画名  ', 'recording_policy': 'public',
                'generate_blog_article': False, **extra,
            },
            instance=instance if instance is not None else EventDetail(event=self.event),
            request=self.request,
        )

    def test_confirmation_required_for_create_edit_and_type_switch(self):
        instances = [None] + [
            make_event_detail(self.event, detail_type=kind, recording_policy='public')
            for kind in ('LT', 'BLOG', 'SPECIAL')
        ]
        for instance in instances:
            with self.subTest(instance=instance, policy='public'):
                form = self.form(instance=instance, recording_policy='public')
                self.assertFalse(form.is_valid())
                self.assertEqual(form.errors['special_recording_confirmed'], [
                    '特別企画でボットによる自動撮影を許可する時は、出演者全員に確認したことにチェックを入れてください。',
                ])

    def test_confirmed_saves_duration_theme_and_resets_confirmation(self):
        form = self.form(special_recording_confirmed=True, recording_policy='public')
        self.assertTrue(form.is_valid(), form.errors)
        detail = form.save()
        detail.refresh_from_db()
        self.assertEqual(detail.duration, 95)
        self.assertEqual(detail.theme, 'パネルの企画名')
        self.assertEqual(detail.recording_policy, 'public')
        self.assertEqual(detail.speaker, '')
        self.assertFalse(detail.can_auto_generate_article)
        reopened = EventDetailForm(instance=detail, request=self.request)
        self.assertFalse(reopened['special_recording_confirmed'].value())
        update = self.form(instance=detail, special_recording_confirmed=True)
        self.assertTrue(update.is_valid(), update.errors)
        update.save()

    def test_allowed_policy_is_rejected_by_form(self):
        form = self.form(special_recording_confirmed=True, recording_policy='allowed')
        self.assertFalse(form.is_valid())
        self.assertIn('recording_policy', form.errors)

    def test_forbidden_and_presentation_need_no_confirmation(self):
        for extra in (
            {'recording_policy': 'forbidden'},
            {'detail_type': 'LT', 'theme': '発表テーマ', 'speaker': '発表者', 'article_consent': 'ok'},
        ):
            with self.subTest(extra=extra):
                form = self.form(**extra)
                self.assertTrue(form.is_valid(), form.errors)
                form.save()

    def test_confirmed_type_switch_can_be_saved(self):
        for kind in ('LT', 'BLOG'):
            with self.subTest(kind=kind):
                detail = make_event_detail(self.event, detail_type=kind)
                form = self.form(instance=detail, special_recording_confirmed=True)
                self.assertTrue(form.is_valid(), form.errors)
                form.save()
                detail.refresh_from_db()
                self.assertEqual(detail.detail_type, 'SPECIAL')
                self.assertEqual(detail.recording_policy, 'public')
                self.assertEqual(detail.duration, 95)

    def test_recording_disabled_ignores_submitted_policy_and_hides_confirmation(self):
        self.community.recording_allowed = False
        self.community.save(update_fields=['recording_allowed'])
        for instance in (None, make_event_detail(self.event, detail_type='SPECIAL', recording_policy='public')):
            with self.subTest(instance=instance):
                form = self.form(instance=instance)
                self.assertNotIn('recording_policy', form.fields)
                self.assertNotIn('special_recording_confirmed', form.fields)
                self.assertTrue(form.is_valid(), form.errors)
                detail = form.save()
                detail.refresh_from_db()
                self.assertEqual(detail.recording_policy, 'forbidden')

    def test_special_initial_policy_is_forbidden(self):
        form = EventDetailForm(instance=EventDetail(event=self.event, detail_type='SPECIAL'))
        self.assertEqual(form['recording_policy'].value(), 'forbidden')

    def test_theme_uses_title_or_fallback_and_truncates(self):
        for title, expected in (('', 'Special Event'), ('　 ', 'Special Event'), ('  題名  ', '題名'), ('題' * 110, '題' * 100)):
            with self.subTest(title=title):
                form = self.form(h1=title, recording_policy='forbidden')
                self.assertTrue(form.is_valid(), form.errors)
                detail = form.save()
                detail.refresh_from_db()
                self.assertEqual(detail.theme, expected)

    def test_applicant_duration_lock_keeps_original_value(self):
        applicant = make_user(user_name='applicant', email='applicant@example.com')
        detail = make_event_detail(self.event, applicant=applicant, duration=35)
        self.request.user = applicant
        form = self.form(instance=detail, recording_policy='forbidden')
        self.assertTrue(form.fields['duration'].disabled)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().duration, 35)

    @patch('event.forms.event_detail.is_event_detail_datetime_locked', return_value=True)
    def test_datetime_lock_keeps_duration_and_rejects_tampering(self, _locked):
        detail = make_event_detail(self.event, detail_type='SPECIAL', duration=35)
        form = self.form(instance=detail, duration=35, recording_policy='forbidden')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().duration, 35)
        form = self.form(instance=detail, duration=95, recording_policy='forbidden')
        self.assertFalse(form.is_valid())
        self.assertIn('duration', form.errors)


class SpecialRecordingAdminTest(TestCase):
    def setUp(self):
        self.event = make_event(make_community())

    def form(self, instance=None, **extra):
        return EventDetailAdminForm(instance=instance, data={
            'event': self.event.pk, 'detail_type': 'SPECIAL', 'start_time': '22:00',
            'duration': 60, 'recording_policy': 'public', 'article_consent': 'unanswered',
            'status': 'approved', 'theme': '企画名', 'slide_fetch_attempts': 0,
            'article_generation_attempts': 0, 'article_generation_deferrals': 0, **extra,
        })

    def test_both_admin_paths_use_guarded_form(self):
        inline_form_cls = EventDetailInline(Event, admin.site).form
        admin_form_cls = EventDetailAdmin(EventDetail, admin.site).form
        self.assertIs(inline_form_cls, EventDetailAdminForm)
        self.assertIs(admin_form_cls, EventDetailAdminForm)
        admin_form = admin_form_cls()
        self.assertEqual(admin_form.fields['recording_policy'].label, 'ボットによる自動撮影')
        self.assertIn('ハブの撮影ボット', admin_form.fields['recording_policy'].help_text)
        self.assertIn(
            '集会独自の撮影や参加者の写真撮影を制限する設定ではありません',
            admin_form.fields['recording_policy'].help_text,
        )

    def test_create_non_forbidden_and_type_or_policy_changes_are_rejected(self):
        for policy in ('public', 'allowed'):
            for instance in (None,
                make_event_detail(self.event, detail_type='LT', recording_policy=policy),
                make_event_detail(self.event, detail_type='SPECIAL', recording_policy='forbidden'),
                make_event_detail(self.event, detail_type='SPECIAL', recording_policy='allowed' if policy == 'public' else 'public'),
            ):
                with self.subTest(instance=instance, policy=policy):
                    form = self.form(instance, recording_policy=policy)
                    self.assertFalse(form.is_valid())
                    self.assertIn('recording_policy', form.errors)
                    if policy == 'public':
                        self.assertEqual(
                            form.errors['recording_policy'],
                            ['特別企画のボットによる自動撮影は、主催者の発表の編集画面で出演者全員の確認をつけて設定してください'],
                        )

    def test_forbidden_creation_and_unchanged_recording_can_be_saved(self):
        form = self.form(recording_policy='forbidden')
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        for policy in ('public', 'allowed'):
            detail = make_event_detail(self.event, detail_type='SPECIAL', recording_policy=policy)
            form = self.form(detail, recording_policy=policy, theme='修正後の企画名')
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
            detail.refresh_from_db()
            self.assertEqual(detail.theme, '修正後の企画名')


class SpecialRecordingScreenTest(TestCase):
    def setUp(self):
        self.owner = make_discord_linked_user()
        self.community = make_community(owner=self.owner, recording_allowed=True)
        self.event = make_event(self.community)
        self.client.force_login(self.owner)
        self.create_url = reverse('event:detail_create', kwargs={'event_pk': self.event.pk})

    def payload(self, **extra):
        return {
            'detail_type': 'SPECIAL', 'start_time': '22:00', 'duration': 90,
            'recording_policy': 'public', 'special_recording_confirmed': True,
            'h1': 'パネル', **extra,
        }

    def test_special_fields_and_link_render_and_errors_show_next_to_checkbox(self):
        response = self.client.post(self.create_url, self.payload(special_recording_confirmed=False))
        self.assertEqual(response.status_code, 200)
        for text in ('name="recording_policy"', 'name="duration"', 'name="special_recording_confirmed"',
                     '/guide/lt/create/#パネルを撮影する時の進め方',
                     '出演者全員に、ボットによる自動撮影と公開の可否を確認しました',
                     '1 人でもボットによる自動撮影を望まない人がいる時は「許可しない（ボットで撮影しない）」を選んでください。'):
            self.assertContains(response, text)
        self.assertContains(
            response,
            '特別企画でボットによる自動撮影を許可する時は、出演者全員に確認したことにチェックを入れてください。',
        )
        self.assertContains(response, "show: ['h1', 'thumbnail_image', 'contents', 'start_time', 'duration', 'recording_policy', 'special_recording_confirmed']")
        self.assertContains(response, 'if (forbidden) forbidden.checked = true;')
        self.assertContains(response, "hide: ['special_recording_confirmed']")
        guide = self.client.get('/guide/lt/create/')
        self.assertContains(guide, 'id="パネルを撮影する時の進め方"')

    def test_recording_disabled_renders_neither_policy_nor_confirmation(self):
        self.community.recording_allowed = False
        self.community.save(update_fields=['recording_allowed'])
        response = self.client.get(self.create_url)
        self.assertNotContains(response, '<input type="radio" name="recording_policy"')
        self.assertNotContains(response, 'name="special_recording_confirmed"')

    def test_enabled_create_and_edit_emit_one_structured_log_each(self):
        for policy in ('public',):
            with self.subTest(policy=policy):
                with self.assertLogs('event.views.crud_event_detail', level='INFO') as logs:
                    response = self.client.post(self.create_url, self.payload(recording_policy=policy))
                self.assertEqual(response.status_code, 302)
                detail = EventDetail.objects.latest('pk')
                record, = logs.records
                self.assertEqual(record.event, 'special_recording_enabled')
                self.assertEqual(record.event_detail_id, detail.pk)
                self.assertEqual(record.community_id, self.community.pk)
                self.assertEqual(record.user_id, self.owner.pk)
                self.assertEqual(record.recording_policy, policy)
                with self.assertLogs('event.views.crud_event_detail', level='INFO') as logs:
                    response = self.client.post(reverse('event:detail_update', kwargs={'pk': detail.pk}), self.payload(recording_policy=policy))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(len(logs.records), 1)

    def test_forbidden_invalid_and_presentation_saves_emit_no_special_log(self):
        for extra, expected in (
            ({'recording_policy': 'forbidden'}, 302),
            ({'special_recording_confirmed': False}, 200),
            ({'detail_type': 'LT', 'theme': '発表', 'speaker': '発表者', 'article_consent': 'ok'}, 302),
        ):
            with self.subTest(extra=extra):
                with self.assertNoLogs('event.views.crud_event_detail', level='INFO'):
                    response = self.client.post(self.create_url, self.payload(**extra))
                self.assertEqual(response.status_code, expected)
