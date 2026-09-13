"""情報編集を伴う集会再開の権限・保存・遷移の回帰テスト。"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from community.models import CommunityMember
from tests.factories import make_community


@override_settings(SOCIALACCOUNT_PROVIDERS={'discord': {'APPS': [{
    'client_id': 'test', 'secret': 'test', 'key': '',
}]}})
class ReopenCommunityTest(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(email='reopen@example.com', user_name='再開主催者')
        self.community = make_community(
            name='終了した集会', end_at=timezone.localdate() - timedelta(days=30),
            frequency='毎週', organizers='主催者', status='approved',
        )
        CommunityMember.objects.create(community=self.community, user=self.owner, role='owner')
        self.url = reverse('community:reopen', args=[self.community.pk])
        self.done = reverse('community:reopen_complete', args=[self.community.pk])
        self.data = {
            'name': '再開した集会', 'start_time': '21:00', 'duration': '90',
            'weekdays': ['Mon'], 'frequency': '毎月', 'organizers': '新しい主催者',
            'description': '10月から再開します', 'platform': 'All', 'tags': ['academic'],
        }
        self.client.force_login(self.owner)

    def test_account_has_action_and_get_prefills_without_reopening(self):
        response = self.client.get(reverse('account:settings'))
        self.assertContains(response, self.url)
        response = self.client.get(self.url)
        self.assertContains(response, 'この内容で再開する')
        self.assertContains(response, 'value="終了した集会"')
        self.assertContains(response, '自動復元されません')
        self.community.refresh_from_db()
        self.assertIsNotNone(self.community.end_at)

    def test_update_and_reopen_together_with_correct_community_selected(self):
        other = make_community(name='別の集会')
        CommunityMember.objects.create(community=other, user=self.owner, role='owner')
        session = self.client.session
        session['active_community_id'] = other.pk
        session.save()
        with patch('community.views.manage.refresh_calendar_entry_and_event_cache') as refresh:
            response = self.client.post(self.url, self.data)
        self.assertRedirects(response, self.done)
        refresh.assert_called_once()
        self.community.refresh_from_db()
        other.refresh_from_db()
        self.assertIsNone(self.community.end_at)
        self.assertEqual(self.community.name, self.data['name'])
        self.assertEqual(self.community.description, self.data['description'])
        self.assertEqual(self.community.weekdays, ['Mon'])
        self.assertEqual(self.community.status, 'approved')
        self.assertEqual(other.name, '別の集会')
        self.assertEqual(self.client.session['active_community_id'], self.community.pk)
        self.assertEqual(self.community.events.count(), 0)
        self.assertEqual(self.community.members.count(), 1)
        response = self.client.get(self.done)
        self.assertContains(response, '開催予定を登録する')
        self.assertContains(response, f'name="community_id" value="{self.community.pk}"')
        response = self.client.post(reverse('community:switch'), {
            'community_id': self.community.pk, 'redirect_to': reverse('event:calendar_create'),
        })
        self.assertRedirects(response, reverse('event:calendar_create'), fetch_redirect_response=False)

    def test_invalid_form_does_not_update_or_reopen(self):
        response = self.client.post(self.url, {**self.data, 'name': ''})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '入力内容にエラーがあります')
        self.community.refresh_from_db()
        self.assertIsNotNone(self.community.end_at)
        self.assertEqual(self.community.description, '')

    def test_failed_related_update_rolls_back_reopening(self):
        with patch('community.views.manage.refresh_calendar_entry_and_event_cache', side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, self.data)
        self.community.refresh_from_db()
        self.assertIsNotNone(self.community.end_at)
        self.assertEqual(self.community.name, '終了した集会')

    def test_repeated_post_does_not_overwrite(self):
        self.client.post(self.url, self.data)
        self.client.post(self.url, {**self.data, 'name': '古いフォーム'})
        self.community.refresh_from_db()
        self.assertEqual(self.community.name, self.data['name'])

    def test_staff_and_nonmembers_cannot_reopen(self):
        for role in ('staff', None):
            user = get_user_model().objects.create_user(email=f'{role}@example.com', user_name=str(role))
            if role:
                CommunityMember.objects.create(community=self.community, user=user, role=role)
            self.client.force_login(user)
            for url in (self.url, self.done):
                self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(self.url, self.data).status_code, 403)
            self.assertNotContains(self.client.get(reverse('account:settings')), self.url)
        self.community.refresh_from_db()
        self.assertIsNotNone(self.community.end_at)

    def test_anonymous_and_csrf(self):
        anonymous = Client()
        self.assertEqual(anonymous.get(self.url).status_code, 302)
        self.assertEqual(anonymous.post(self.url, self.data).status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(self.url, self.data).status_code, 403)

    def test_nonmember_superuser_does_not_get_registration_link(self):
        admin = get_user_model().objects.create_superuser(email='admin-reopen@example.com', password='test', user_name='管理者')
        self.client.force_login(admin)
        self.client.post(self.url, self.data)
        response = self.client.get(self.done)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '開催予定を登録する')
        self.assertContains(response, reverse('community:detail', args=[self.community.pk]))

    def test_pending_status_preserved_and_explained(self):
        self.community.status = 'pending'
        self.community.save()
        self.client.post(self.url, self.data)
        self.community.refresh_from_db()
        self.assertEqual(self.community.status, 'pending')
        self.assertContains(self.client.get(self.done), '承認されるまでは公開されません')

    def test_future_end_date_is_explained(self):
        self.community.end_at = timezone.localdate() + timedelta(days=30)
        self.community.save()
        self.assertContains(self.client.get(self.url), '終了予定を取り消します')

    def test_complete_redirects_if_still_closed(self):
        self.assertRedirects(self.client.get(self.done), self.url)

    def test_admin_only_tags_and_existing_poster_are_preserved(self):
        self.community.tags = ['academic', 'partner']
        self.community.poster_image = 'poster/existing.png'
        self.community.save()
        self.assertNotContains(self.client.get(self.url), 'value="partner"')
        self.client.post(self.url, self.data)
        self.community.refresh_from_db()
        self.assertEqual(self.community.tags, ['academic', 'partner'])
        self.assertEqual(self.community.poster_image.name, 'poster/existing.png')

    def test_invalid_uploaded_poster_does_not_reopen(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        data = {**self.data, 'poster_image': SimpleUploadedFile('bad.png', b'not an image', content_type='image/png')}
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors['poster_image'])
        self.community.refresh_from_db()
        self.assertIsNotNone(self.community.end_at)

    def test_new_poster_can_be_uploaded_while_reopening(self):
        import io
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        from tempfile import TemporaryDirectory
        buffer = io.BytesIO()
        Image.new('RGB', (32, 32), 'blue').save(buffer, format='PNG')
        data = {**self.data, 'poster_image': SimpleUploadedFile('poster.png', buffer.getvalue(), content_type='image/png')}
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(self.url, data)
            self.assertRedirects(response, self.done)
            self.community.refresh_from_db()
            self.assertTrue(self.community.poster_image.storage.exists(self.community.poster_image.name))
            self.assertIsNone(self.community.end_at)
