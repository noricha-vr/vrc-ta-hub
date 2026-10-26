"""ステージ登録用の本文・権限・一回生成を外部通信なしで検証する。"""

import base64
from datetime import date, time, timedelta
from io import BytesIO
import os
import tempfile
from unittest.mock import Mock, patch

from django.contrib.admin.sites import AdminSite
from django.core.files.base import ContentFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
import requests

from community.models import Community, CommunityMember
from tests.factories import make_community, make_user
from vket.admin import VketParticipationAdmin
from vket.models import VketCollaboration, VketParticipation, VketPresentation
from vket.stage_copy import build_stage_copy, thumbnail_prompt


def image_bytes(size=(1280, 720)):
    output = BytesIO()
    Image.new('RGB', size, 'white').save(output, format='PNG')
    return output.getvalue()


class StageCopyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user(user_name='copy_owner', email='copy_owner@example.com')
        cls.staff = make_user(user_name='copy_staff', email='copy_staff@example.com')
        cls.outsider = make_user(user_name='copy_outsider', email='copy_outsider@example.com')
        cls.admin = make_user(user_name='copy_admin', email='copy_admin@example.com', is_staff=True)
        cls.community = make_community(
            owner=cls.owner, name='技術集会', description='集会の紹介です。毎週開催しています。',
            sns_url='https://example.com/group', discord='https://discord.example.com/group', twitter_hashtag='技術集会',
        )
        CommunityMember.objects.create(user=cls.staff, community=cls.community, role=CommunityMember.Role.STAFF)
        cls.collaboration = VketCollaboration.objects.create(
            slug='stage-copy-test', name='ステージ告知テスト', phase=VketCollaboration.Phase.ANNOUNCEMENT,
            period_start=timezone.localdate(), period_end=timezone.localdate() + timedelta(days=7),
            registration_deadline=timezone.localdate(), lt_deadline=timezone.localdate(),
            settings_json={
                'stage_registration_open': True,
                'stage_registration_guidance': {'venue': 'テスト会場', 'tag': 'テストタグ'},
                'stage_copy': {'title_suffix': '技術学術WEEK', 'week_description': 'みんなで学ぶ週間です。', 'hashtags': ['学術']},
            },
        )
        cls.participation = VketParticipation.objects.create(
            collaboration=cls.collaboration, community=cls.community, progress=VketParticipation.Progress.APPLIED,
            confirmed_date=date(2026, 12, 12), confirmed_start_time=time(21, 0), confirmed_duration=60,
        )

    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        storage = override_settings(MEDIA_ROOT=self.media.name, STORAGES={
            'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
        })
        storage.enable()
        self.addCleanup(storage.disable)
        self.client.force_login(self.owner)
        self.kwargs = {'pk': self.collaboration.pk, 'participation_id': self.participation.pk}
        self.url = reverse('vket:stage_copy', kwargs=self.kwargs)
        self.generate_url = reverse('vket:stage_thumbnail_generate', kwargs=self.kwargs)
        self.download_url = reverse('vket:stage_thumbnail_download', kwargs=self.kwargs)
        poster = self.community.poster_image
        poster.save('poster.png', ContentFile(image_bytes((64, 64))), save=False)
        Community.objects.filter(pk=self.community.pk).update(poster_image=poster.name)
        self.community.refresh_from_db()
        response = Mock()
        response.json.return_value = {'choices': [{'message': {'images': [{'image_url': {
            'url': 'data:image/png;base64,' + base64.b64encode(image_bytes()).decode('ascii'),
        }}]}}]}
        self.api = patch('vket.stage_thumbnail.requests.post', return_value=response).start()
        self.addCleanup(patch.stopall)

    def copy_values(self, response):
        return {field['id']: field['value'] for field in response.context['copy_fields']}

    def test_fields_and_confirmed_presentations(self):
        VketPresentation.objects.create(
            participation=self.participation, speaker='発表者', theme='技術テーマ', status=VketPresentation.Status.CONFIRMED,
        )
        VketPresentation.objects.create(participation=self.participation, speaker='未確定', theme='下書き')
        response = self.client.get(self.url)
        values = self.copy_values(response)
        self.assertEqual(list(values), ['url', 'title', 'body', 'start', 'end', 'language', 'venue', 'tag'])
        self.assertTrue(values['url'].startswith('https://'))
        self.assertTrue(values['url'].endswith(reverse('community:detail', kwargs={'pk': self.community.pk})))
        self.assertEqual(values['title'], '技術集会 ｜ 技術学術WEEK')
        self.assertEqual(values['start'], '2026/12/12 21:00')
        self.assertEqual(values['end'], '2026/12/12 22:00')
        self.assertEqual(values['language'], '日本語')
        self.assertEqual(values['venue'], 'テスト会場')
        self.assertEqual(values['tag'], 'テストタグ')
        self.assertIn('12月12日（土）21:00〜22:00', values['body'])
        self.assertIn('発表者 - 技術テーマ', values['body'])
        self.assertNotIn('下書き', values['body'])
        self.assertIn('■ 技術学術WEEKとは', values['body'])
        self.assertIn('#技術集会 #学術', values['body'])
        self.assertIn('X：https://example.com/group', values['body'])
        self.assertIn('Discord：https://discord.example.com/group', values['body'])
        self.assertContains(response, 'data-copy-target=', count=9)
        self.assertContains(response, 'OGP')
        self.api.assert_not_called()

    def test_long_description_and_presentations_fit_body_without_markdown(self):
        self.community.description = '# 紹介\n**大切な集会**です。\n' * 1000
        self.community.save(update_fields=['description'])
        for order in range(20):
            VketPresentation.objects.create(
                participation=self.participation, order=order, speaker='登壇者' * 50,
                theme='発表テーマ' * 30, status=VketPresentation.Status.CONFIRMED,
            )
        values = self.copy_values(self.client.get(self.url))
        self.assertLessEqual(len(values['body']), 1000)
        self.assertNotIn('**', values['body'])
        self.assertNotIn('# 紹介', values['body'])
        self.assertIn('集会ページ：', values['body'])
        self.assertIn('■ 日時', values['body'])

    def test_description_is_cut_at_sentence(self):
        self.community.description = 'あ' * 200 + '。' + 'い' * 300
        self.community.save(update_fields=['description'])
        values = self.copy_values(self.client.get(self.url))
        self.assertTrue(values['body'].startswith('あ' * 200 + '。\n\n'))

    def test_missing_schedule_never_uses_requested_schedule(self):
        self.participation.confirmed_start_time = None
        self.participation.requested_start_time = time(18)
        self.participation.save()
        values = self.copy_values(self.client.get(self.url))
        self.assertEqual(values['start'], '日程の確定後に表示します')
        self.assertEqual(values['end'], values['start'])
        self.assertIn(values['start'], values['body'])
        self.assertNotIn('18:00', values['body'])

    def test_end_datetime_rolls_over_midnight(self):
        self.participation.confirmed_start_time = time(23, 30)
        self.participation.save()
        values = self.copy_values(self.client.get(self.url))
        self.assertEqual(values['end'], '2026/12/13 00:30')
        self.assertIn('12月13日 00:30', values['body'])

    def test_config_fallbacks_and_missing_guidance(self):
        for settings in (None, [], {}, {'stage_copy': []}, {'stage_copy': {'title_suffix': 1}}):
            with self.subTest(settings=settings):
                self.collaboration.settings_json = settings
                self.collaboration.save()
                values = self.copy_values(self.client.get(self.url))
                self.assertEqual(values['title'], '技術集会 ｜ ステージ告知テスト')
                self.assertNotIn('とは\n', values['body'])
                self.assertIn('発表者とテーマは決まりしだいお知らせします。', values['body'])

    def test_hashtags_fall_back_to_collaboration_and_schedule_link_stays(self):
        self.collaboration.settings_json = {'stage_registration_open': True}
        self.collaboration.hashtags = ['#Vketステージ', '#技術学術WEEK']
        self.collaboration.save()
        body = self.copy_values(self.client.get(self.url))['body']
        self.assertIn('#技術集会 #Vketステージ #技術学術WEEK', body)
        self.assertIn('スケジュール：http', body)

    def test_description_keeps_line_breaks(self):
        self.community.description = '一行目の紹介。\r\n**二行目**の紹介。\n\n\n\n三行目。'
        self.community.save()
        body = self.copy_values(self.client.get(self.url))['body']
        self.assertTrue(body.startswith('一行目の紹介。\n二行目の紹介。\n\n三行目。\n\n■ 日時'))

    def test_extreme_config_is_still_under_1000_characters(self):
        self.collaboration.settings_json = {'stage_copy': {
            'title_suffix': '企画' * 3000, 'week_description': '説明' * 3000, 'hashtags': ['長いタグ' * 3000],
        }}
        self.participation.collaboration = self.collaboration
        values = build_stage_copy(self.participation, {'venue': '会場' * 3000})
        self.assertLessEqual(len(values['copy_fields'][2]['value']), 1000)

    def test_edit_prompt_and_advice(self):
        response = self.client.get(self.url)
        for text in ['ChatGPT / Gemini', '生成した画像とポスターを添付', '1536×864', 'キャラクターのデザインは変えない', '文字の誤字を確かめ']:
            self.assertContains(response, text)
        self.assertTrue(thumbnail_prompt(self.participation, for_edit=True).startswith('直したい点：'))
        self.assertNotIn('直したい点', thumbnail_prompt(self.participation))

    def test_prompt_uses_only_confirmed_themes(self):
        VketPresentation.objects.create(participation=self.participation, theme='確定テーマ', status='confirmed')
        VketPresentation.objects.create(participation=self.participation, theme='未確定テーマ', status='draft')
        prompt = thumbnail_prompt(self.participation)
        self.assertIn('12/12(土) 21:00', prompt)
        self.assertIn('確定テーマ', prompt)
        self.assertNotIn('未確定テーマ', prompt)
        self.assertIn('画像内の文字にはしない', prompt)

    def test_staff_and_admin_can_view(self):
        for user in (self.staff, self.admin):
            with self.subTest(user=user):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_nonmember_cannot_view_generate_or_download(self):
        self.client.force_login(self.outsider)
        for method, url in [('get', self.url), ('post', self.generate_url), ('get', self.download_url)]:
            with self.subTest(url=url):
                self.assertIn(getattr(self.client, method)(url).status_code, [403, 404])
        self.api.assert_not_called()

    def test_member_of_another_community_is_rejected(self):
        make_community(owner=self.outsider, name='別の集会')
        self.test_nonmember_cannot_view_generate_or_download()

    def test_anonymous_redirects_to_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.generate_url).status_code, 302)
        self.api.assert_not_called()

    def test_hidden_collaboration_is_not_accessible(self):
        self.collaboration.phase = VketCollaboration.Phase.DRAFT
        self.collaboration.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.generate_url).status_code, 404)
        self.api.assert_not_called()

    def test_cross_collaboration_participation_is_not_accessible(self):
        url = reverse('vket:stage_copy', kwargs={**self.kwargs, 'pk': self.collaboration.pk + 1})
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_post_requires_csrf_and_get_never_generates(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(self.generate_url).status_code, 403)
        self.assertEqual(client.get(self.generate_url).status_code, 405)
        self.api.assert_not_called()
        client.get(self.url)
        response = client.post(self.generate_url, {'csrfmiddlewaretoken': client.cookies['csrftoken'].value})
        self.assertEqual(response.status_code, 302)
        self.api.assert_called_once()

    def test_status_links_to_copy_screen(self):
        response = self.client.get(reverse('vket:status', kwargs={'pk': self.collaboration.pk}))
        self.assertContains(response, self.url)

    def test_no_poster_hides_button_and_never_calls_api(self):
        Community.objects.filter(pk=self.community.pk).update(poster_image='')
        response = self.client.get(self.url)
        self.assertContains(response, '先に集会のポスターを登録してください')
        self.assertContains(response, reverse('community:update'))
        self.assertNotContains(response, 'id="stage-thumbnail-form"')
        self.client.post(self.generate_url)
        self.api.assert_not_called()
        self.participation.refresh_from_db()
        self.assertIsNone(self.participation.stage_thumbnail_requested_at)

    def test_registration_closed_does_not_generate(self):
        self.collaboration.settings_json = {'stage_registration_open': False}
        self.collaboration.save()
        self.client.post(self.generate_url)
        self.api.assert_not_called()
        self.assertNotContains(self.client.get(self.url), 'id="stage-thumbnail-form"')

    def test_success_saves_jpeg_timestamps_and_download(self):
        with patch('vket.stage_thumbnail.logger') as logger:
            response = self.client.post(self.generate_url)
        self.assertRedirects(response, f'{self.url}#stage-thumbnail')
        self.participation.refresh_from_db()
        self.assertIsNotNone(self.participation.stage_thumbnail_requested_at)
        self.assertIsNotNone(self.participation.stage_thumbnail_generated_at)
        self.assertTrue(self.participation.stage_thumbnail.name.endswith('.jpg'))
        with self.participation.stage_thumbnail.open('rb') as file:
            with Image.open(file) as image:
                self.assertEqual(image.format, 'JPEG')
                self.assertEqual(image.size, (1280, 720))
        log = logger.info.call_args
        self.assertEqual(log.args, ('vket_stage_thumbnail',))
        self.assertEqual(log.kwargs['result'], 'generated')
        self.assertEqual(log.kwargs['community_id'], self.community.pk)
        self.assertEqual(log.kwargs['participation_id'], self.participation.pk)
        self.assertGreaterEqual(log.kwargs['duration_seconds'], 0)
        download = self.client.get(self.download_url)
        self.assertEqual(download['Content-Type'], 'image/jpeg')
        self.assertIn('attachment;', download['Content-Disposition'])
        self.assertTrue(b''.join(download.streaming_content).startswith(b'\xff\xd8'))
        self.assertNotContains(self.client.get(self.url), 'id="stage-thumbnail-form"')
        self.api.assert_called_once()
        call = self.api.call_args.kwargs
        self.assertEqual(call['timeout'], 110)
        self.assertEqual(call['json']['model'], 'openai/gpt-5.4-image-2')
        self.assertEqual(call['json']['modalities'], ['image', 'text'])
        self.assertEqual(call['json']['image_config'], {'aspect_ratio': '16:9'})
        self.assertTrue(call['json']['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,'))

    def test_double_post_only_calls_api_once(self):
        self.client.post(self.generate_url)
        self.client.post(self.generate_url)
        self.api.assert_called_once()

    def test_post_while_first_request_is_running_only_calls_api_once(self):
        response = self.api.return_value

        def during_generation(*args, **kwargs):
            nested_client = Client()
            nested_client.force_login(self.staff)
            nested_response = nested_client.post(self.generate_url)
            self.assertEqual(nested_response.status_code, 302)
            return response

        self.api.side_effect = during_generation
        self.client.post(self.generate_url)
        self.api.assert_called_once()

    def test_failure_releases_reservation_and_hides_exception(self):
        self.api.side_effect = requests.Timeout('secret-value-do-not-log')
        with patch('vket.stage_thumbnail.logger') as logger:
            response = self.client.post(self.generate_url, follow=True)
        self.participation.refresh_from_db()
        self.assertIsNone(self.participation.stage_thumbnail_requested_at)
        self.assertIsNone(self.participation.stage_thumbnail_generated_at)
        self.assertFalse(self.participation.stage_thumbnail)
        self.assertNotContains(response, 'secret-value-do-not-log')
        self.assertNotIn('secret-value-do-not-log', str(logger.mock_calls))
        self.assertTrue(any(call.kwargs.get('result') == 'failed' for call in logger.info.call_args_list))
        self.api.side_effect = None
        self.client.post(self.generate_url)
        self.participation.refresh_from_db()
        self.assertIsNotNone(self.participation.stage_thumbnail_generated_at)

    def test_bad_response_and_non_widescreen_images_release_reservation(self):
        for url in ['https://example.com/image.png', 'data:image/png;base64,invalid',
                    'data:image/png;base64,' + base64.b64encode(image_bytes((64, 64))).decode('ascii')]:
            with self.subTest(url=url[:40]):
                self.api.return_value.json.return_value = {'choices': [{'message': {'images': [{'image_url': {'url': url}}]}}]}
                self.client.post(self.generate_url)
                self.participation.refresh_from_db()
                self.assertIsNone(self.participation.stage_thumbnail_requested_at)

    def test_download_requires_generated_image(self):
        self.assertEqual(self.client.get(self.download_url).status_code, 404)

    def test_reserved_participation_cannot_start_another_generation(self):
        VketParticipation.objects.filter(pk=self.participation.pk).update(stage_thumbnail_requested_at=timezone.now())
        response = self.client.get(self.url)
        self.assertContains(response, 'サムネイルを生成中')
        self.assertNotContains(response, 'id="stage-thumbnail-form"')
        self.client.post(self.generate_url)
        self.api.assert_not_called()

    def test_http_error_releases_reservation(self):
        self.api.return_value.raise_for_status.side_effect = requests.HTTPError('private-response')
        self.client.post(self.generate_url)
        self.participation.refresh_from_db()
        self.assertIsNone(self.participation.stage_thumbnail_requested_at)
        self.assertIsNone(self.participation.stage_thumbnail_generated_at)

    def test_missing_key_does_not_call_api_and_releases_reservation(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': ''}):
            self.client.post(self.generate_url)
        self.participation.refresh_from_db()
        self.assertIsNone(self.participation.stage_thumbnail_requested_at)
        self.api.assert_not_called()

    def test_storage_failure_releases_reservation(self):
        with patch('django.core.files.storage.FileSystemStorage.save', side_effect=OSError('storage-failed')):
            self.client.post(self.generate_url)
        self.participation.refresh_from_db()
        self.assertIsNone(self.participation.stage_thumbnail_requested_at)
        self.assertIsNone(self.participation.stage_thumbnail_generated_at)

    def test_success_cannot_be_regenerated_when_only_reservation_is_cleared(self):
        self.client.post(self.generate_url)
        VketParticipation.objects.filter(pk=self.participation.pk).update(stage_thumbnail_requested_at=None)
        self.client.post(self.generate_url)
        self.api.assert_called_once()

    def test_view_log(self):
        with patch('vket.stage_thumbnail.logger') as logger:
            self.client.get(self.url)
        self.assertEqual(logger.info.call_args.kwargs['result'], 'viewed')

    def test_admin_reset_clears_completed_but_not_running_reservation(self):
        self.client.post(self.generate_url)
        model_admin = VketParticipationAdmin(VketParticipation, AdminSite())
        for field in ('stage_thumbnail_requested_at', 'stage_thumbnail_generated_at'):
            self.assertIn(field, model_admin.readonly_fields)
        request = RequestFactory().post('/admin/')
        with patch.object(model_admin, 'message_user'):
            model_admin.reset_stage_thumbnail(request, VketParticipation.objects.filter(pk=self.participation.pk))
        self.participation.refresh_from_db()
        self.assertIsNone(self.participation.stage_thumbnail_requested_at)
        self.assertIsNone(self.participation.stage_thumbnail_generated_at)
        self.assertFalse(self.participation.stage_thumbnail)
        now = timezone.now()
        VketParticipation.objects.filter(pk=self.participation.pk).update(stage_thumbnail_requested_at=now)
        with patch.object(model_admin, 'message_user'):
            model_admin.reset_stage_thumbnail(request, VketParticipation.objects.filter(pk=self.participation.pk))
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.stage_thumbnail_requested_at, now)
        VketParticipation.objects.filter(pk=self.participation.pk).update(
            stage_thumbnail_requested_at=now - timedelta(minutes=6),
        )
        with patch.object(model_admin, 'message_user'):
            model_admin.reset_stage_thumbnail(request, VketParticipation.objects.filter(pk=self.participation.pk))
        self.participation.refresh_from_db()
        self.assertIsNone(self.participation.stage_thumbnail_requested_at)
