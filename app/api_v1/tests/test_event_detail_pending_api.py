"""承認待ちの発表登録と元投稿リンクのテスト。"""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from event.models import EventDetail
from tests.factories import make_community, make_discord_linked_user, make_event, make_event_detail
from user_account.models import APIKey


class EventDetailPendingAPITest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_discord_linked_user()
        cls.event = make_event(make_community(owner=cls.owner))
        cls.approved = make_event_detail(
            cls.event, applicant=cls.owner, status='approved', theme='承認済みの発表',
            slide_url='https://example.com/approved.pdf',
        )
        _, cls.raw_key = APIKey.create_with_raw_key(user=cls.owner, name='発表登録')
        cls.url = reverse('event-detail-api-list')
        cls.detail_url = reverse('event-detail-api-detail', kwargs={'pk': cls.approved.pk})

    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_X_API_KEY=self.raw_key)
        self.payload = {
            'event': self.event.pk,
            'detail_type': 'LT',
            'speaker': '発表者',
            'theme': '承認待ちの発表',
            'slide_url': 'https://example.com/pending.pdf',
            'status': 'pending',
            'source_url': 'https://example.com/posts/123',
            'source': 'discord_bot',
        }

    def _create_pending(self):
        response = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return EventDetail.objects.get(pk=response.data['id'])

    def test_create_pending_with_source(self):
        detail = self._create_pending()

        self.assertEqual(detail.status, 'pending')
        self.assertEqual(detail.source_url, self.payload['source_url'])
        self.assertEqual(detail.source, 'discord_bot')
        self.assertEqual(detail.recording_policy, EventDetail.RecordingPolicy.FORBIDDEN)

    def test_create_without_status_or_source_preserves_defaults(self):
        for field in ('status', 'source_url', 'source'):
            self.payload.pop(field)

        response = self.client.post(self.url, self.payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        detail = EventDetail.objects.get(pk=response.data['id'])
        self.assertEqual(detail.status, 'approved')
        self.assertEqual(detail.source_url, '')
        self.assertEqual(detail.source, '')

    def test_create_rejects_status_other_than_pending(self):
        count = EventDetail.objects.count()
        for value in ('approved', 'rejected', 'unknown', '', None):
            with self.subTest(status=value):
                response = self.client.post(self.url, {**self.payload, 'status': value}, format='json')

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn('status', response.data)
        self.assertEqual(EventDetail.objects.count(), count)

    def test_update_rejects_approved_and_rejected(self):
        for method in ('put', 'patch'):
            for value in ('approved', 'rejected'):
                with self.subTest(method=method, status=value):
                    response = getattr(self.client, method)(
                        self.detail_url, {**self.payload, 'status': value}, format='json',
                    )

                    self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                    self.assertIn('status', response.data)
        self.approved.refresh_from_db()
        self.assertEqual(self.approved.status, 'approved')
        self.assertEqual(self.approved.theme, '承認済みの発表')

    def test_update_ignores_status_pending(self):
        for method in ('put', 'patch'):
            with self.subTest(method=method):
                body = self.payload if method == 'put' else {'status': 'pending'}
                response = getattr(self.client, method)(self.detail_url, body, format='json')

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.approved.refresh_from_db()
                self.assertEqual(self.approved.status, 'approved')

    def test_update_without_status_preserves_current_status(self):
        self.payload.pop('status')
        for method in ('put', 'patch'):
            for current_status in ('approved', 'pending', 'rejected'):
                with self.subTest(method=method, status=current_status):
                    EventDetail.objects.filter(pk=self.approved.pk).update(status=current_status)

                    response = getattr(self.client, method)(self.detail_url, self.payload, format='json')

                    self.assertEqual(response.status_code, status.HTTP_200_OK)
                    self.approved.refresh_from_db()
                    self.assertEqual(self.approved.status, current_status)
                    self.assertEqual(self.approved.source_url, self.payload['source_url'])
                    self.assertEqual(self.approved.source, 'discord_bot')

    def test_source_fields_allow_empty_values(self):
        self.payload.update(source_url='', source='')
        detail = self._create_pending()

        self.assertEqual(detail.source_url, '')
        self.assertEqual(detail.source, '')

    def test_invalid_source_url_is_rejected(self):
        count = EventDetail.objects.count()
        for value in ('invalid-url', 'javascript:alert(1)'):
            with self.subTest(source_url=value):
                response = self.client.post(self.url, {**self.payload, 'source_url': value}, format='json')

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn('source_url', response.data)
        self.assertEqual(EventDetail.objects.count(), count)

    def test_pending_is_hidden_from_public_api(self):
        pending = self._create_pending()
        self.client.credentials()

        response = self.client.get(reverse('eventdetail-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        items = response.data['results'] if isinstance(response.data, dict) else response.data
        self.assertEqual({item['id'] for item in items}, {self.approved.pk})
        response = self.client.get(reverse('eventdetail-detail', kwargs={'pk': pending.pk}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_pending_is_hidden_from_public_presentation_list(self):
        pending = self._create_pending()
        self.client.credentials()

        response = self.client.get(reverse('event:detail_history'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.context['event_details']), [self.approved])
        self.assertContains(response, self.approved.theme)
        self.assertNotContains(response, pending.theme)

    def test_pending_is_hidden_from_public_event_list(self):
        pending = self._create_pending()
        self.client.credentials()

        response = self.client.get(reverse('event:list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        events = list(response.context['events'])
        self.assertEqual(len(events), 1)
        self.assertEqual(list(events[0].details.all()), [self.approved])
        self.assertContains(response, self.approved.theme)
        self.assertNotContains(response, pending.theme)

    def test_applicant_list_does_not_show_source_link(self):
        pending = self._create_pending()
        EventDetail.objects.filter(pk=pending.pk).update(applicant=self.owner)
        self.client.force_login(self.owner)

        response = self.client.get(reverse('account:lt_application_list'))

        self.assertContains(response, pending.theme)
        self.assertNotContains(response, '元の投稿')
        self.assertNotContains(response, pending.source_url)


class BotPendingReviewTest(TestCase):
    """申請者のいない（Bot が登録した）承認待ちを主催者が処理できること。"""

    def setUp(self):
        self.owner = make_discord_linked_user()
        self.event = make_event(make_community(owner=self.owner))
        self.pending = make_event_detail(
            self.event, applicant=None, status='pending', theme='Bot の発表',
            source_url='https://example.com/posts/123', source='discord_bot',
        )
        self.review_url = reverse('event:lt_application_review', kwargs={'pk': self.pending.pk})
        self.client.force_login(self.owner)

    def _post(self, action, **extra):
        data = {
            'action': action,
            'event': self.event.pk,
            'start_time': self.pending.start_time.strftime('%H:%M'),
            'duration': self.pending.duration,
            'rejection_reason': '',
            **extra,
        }
        with patch('event.notifications.send_mail') as send_mail:
            response = self.client.post(self.review_url, data)
        send_mail.assert_not_called()
        return response

    def test_my_list_shows_review_buttons_and_source(self):
        response = self.client.get(reverse('event:my_list'))

        self.assertContains(response, self.review_url)
        self.assertContains(response, f'data-bs-target="#approveModal{self.pending.pk}"')
        self.assertContains(response, f'data-bs-target="#rejectModal{self.pending.pk}"')
        self.assertContains(response, reverse('event:lt_application_approve', kwargs={'pk': self.pending.pk}))
        self.assertContains(response, reverse('event:lt_application_reject', kwargs={'pk': self.pending.pk}))
        self.assertContains(
            response,
            f'href="{self.pending.source_url}" class="btn btn-outline-secondary btn-sm" '
            'target="_blank" rel="noopener noreferrer"',
        )
        self.assertContains(response, 'Discord から自動で登録')

    def test_review_page_shows_source(self):
        response = self.client.get(self.review_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'href="{self.pending.source_url}" target="_blank" rel="noopener noreferrer"',
        )
        self.assertContains(response, 'Discord から自動で登録')
        self.assertContains(response, 'id="review-form"')

    def test_approve_publishes(self):
        response = self._post('approve')

        self.assertRedirects(response, reverse('event:my_list'), fetch_redirect_response=False)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, 'approved')
        self.client.logout()
        public = self.client.get(reverse('event:list'))
        self.assertContains(public, 'Bot の発表')

    def test_reject(self):
        response = self._post('reject', rejection_reason='重複')

        self.assertRedirects(response, reverse('event:my_list'), fetch_redirect_response=False)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, 'rejected')
        self.assertEqual(self.pending.rejection_reason, '重複')

    def test_modal_approve_and_reject_views(self):
        with patch('event.notifications.send_mail') as send_mail:
            self.client.post(reverse('event:lt_application_approve', kwargs={'pk': self.pending.pk}))
            other = make_event_detail(self.event, applicant=None, status='pending', theme='別の発表')
            self.client.post(
                reverse('event:lt_application_reject', kwargs={'pk': other.pk}),
                {'rejection_reason': '対象外'},
            )
        send_mail.assert_not_called()
        self.pending.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.pending.status, 'approved')
        self.assertEqual(other.status, 'rejected')

    def test_my_list_keeps_status_and_review_link_after_processing(self):
        for processed_status, label in (('approved', '承認済み'), ('rejected', '却下')):
            with self.subTest(status=processed_status):
                EventDetail.objects.filter(pk=self.pending.pk).update(status=processed_status)

                response = self.client.get(reverse('event:my_list'))

                self.assertContains(response, self.review_url)
                self.assertContains(response, 'Discord から自動で登録')
                self.assertContains(response, label)
                self.assertNotContains(response, f'data-bs-target="#approveModal{self.pending.pk}"')
                self.assertNotContains(response, f'data-bs-target="#rejectModal{self.pending.pk}"')


class BotPendingReviewPermissionTest(TestCase):
    """申請者のいない承認待ちを、未ログイン・別の集会のユーザーが確認・処理できないこと。"""

    def setUp(self):
        owner = make_discord_linked_user()
        self.pending = make_event_detail(
            make_event(make_community(owner=owner)), applicant=None, status='pending',
            theme='Bot の発表', source='discord_bot',
        )
        self.other = make_discord_linked_user(user_name='otheruser', email='other@example.com')
        make_community(name='別の集会', owner=self.other)
        kwargs = {'pk': self.pending.pk}
        self.review_url = reverse('event:lt_application_review', kwargs=kwargs)
        self.approve_url = reverse('event:lt_application_approve', kwargs=kwargs)
        self.reject_url = reverse('event:lt_application_reject', kwargs=kwargs)

    def _assert_cannot_process(self):
        with patch('event.notifications.send_mail'):
            self.client.post(self.review_url, {'action': 'approve'})
            self.client.post(self.approve_url)
            self.client.post(self.reject_url, {'rejection_reason': '対象外'})
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, 'pending')

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.review_url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)
        self._assert_cannot_process()

    def test_other_community_user_is_denied(self):
        self.client.force_login(self.other)

        response = self.client.get(self.review_url)

        self.assertEqual(response.status_code, 302)
        self._assert_cannot_process()
