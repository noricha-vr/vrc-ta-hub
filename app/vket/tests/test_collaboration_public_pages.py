"""Vketコラボ機能のテスト."""

from datetime import timedelta
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from tests.factories import make_discord_linked_user
from vket.models import (
    RESERVED_COLLABORATION_SLUGS,
    VketCollaboration,
)


User = get_user_model()


class CollaborationPublicPagesTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-2026-summer',
            name='Vket 2026 Summer 技術学術WEEK',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.ENTRY_OPEN,
            hashtags=['#Vketステージ', '#Vket技術学術WEEK'],
        )
        self.draft_collaboration = VketCollaboration.objects.create(
            slug='vket-2026-draft',
            name='Vket Draft',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.DRAFT,
        )

    def test_list_page(self):
        client = Client()
        response = client.get(reverse('vket:list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.collaboration.name)
        self.assertContains(response, f'href="/vket/{self.collaboration.slug}/"')
        # 下書きは表示されない
        self.assertNotContains(response, self.draft_collaboration.name)

    def test_detail_page(self):
        client = Client()
        response = client.get(reverse('vket:detail', kwargs={'slug': self.collaboration.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.collaboration.name)
        self.assertEqual(response.context['og_url_path'], f'/vket/{self.collaboration.slug}/')

    def test_slug_that_collides_with_other_vket_urls_is_rejected(self):
        for slug in ('2', '2026', 'status', 'ack', 'auto-confirm'):
            with self.subTest(slug=slug):
                self.collaboration.slug = slug
                with self.assertRaises(ValidationError) as raised:
                    self.collaboration.full_clean()
                self.assertIn('slug', raised.exception.message_dict)
        self.collaboration.slug = '2026-winter'
        self.collaboration.full_clean()

    def test_reserved_slugs_cover_every_fixed_vket_path(self):
        from vket import urls as vket_urls
        fixed = {
            str(pattern.pattern).split('/')[0]
            for pattern in vket_urls.urlpatterns
            if str(pattern.pattern) and '<' not in str(pattern.pattern).split('/')[0]
        }
        self.assertEqual(fixed, set(RESERVED_COLLABORATION_SLUGS))

    def test_get_absolute_url_uses_slug(self):
        self.assertEqual(self.collaboration.get_absolute_url(), '/vket/vket-2026-summer/')
        self.collaboration.slug = '2026-summer'
        self.assertEqual(self.collaboration.get_absolute_url(), '/vket/2026-summer/')

    def test_legacy_detail_redirects_permanently(self):
        response = self.client.get(f'/vket/{self.collaboration.pk}/')
        self.assertRedirects(response, self.collaboration.get_absolute_url(), status_code=301)

    def test_legacy_detail_preserves_query_string(self):
        query = 'utm_source=discord&tag=a%2Fb&tag=c+d&empty='
        response = self.client.get(f'/vket/{self.collaboration.pk}/?{query}')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], f'{self.collaboration.get_absolute_url()}?{query}')

    def test_legacy_detail_redirect_uses_updated_slug(self):
        self.collaboration.slug = '2026-summer'
        self.collaboration.save(update_fields=['slug'])
        response = self.client.get(f'/vket/{self.collaboration.pk}/')
        self.assertRedirects(response, '/vket/2026-summer/', status_code=301)

    def test_draft_urls_are_404_for_anonymous_and_regular_users(self):
        user = make_discord_linked_user()
        for authenticated in (False, True):
            if authenticated:
                self.client.force_login(user)
            for url in (self.draft_collaboration.get_absolute_url(), f'/vket/{self.draft_collaboration.pk}/'):
                with self.subTest(authenticated=authenticated, url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 404)
                    self.assertNotIn('Location', response)

    def test_admin_can_access_draft_detail_and_legacy_redirect(self):
        for field in ('is_staff', 'is_superuser'):
            with self.subTest(field=field):
                user = make_discord_linked_user(
                    user_name=field, email=f'{field}@example.com', **{field: True},
                )
                self.client.force_login(user)
                response = self.client.get(reverse('vket:detail_legacy', args=[self.draft_collaboration.pk]))
                self.assertRedirects(response, self.draft_collaboration.get_absolute_url(), status_code=301)

    def test_missing_collaboration_urls_are_404(self):
        for url in ('/vket/missing-collaboration/', '/vket/999999/'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn('Location', response)

    def test_fixed_and_nested_routes_are_not_shadowed(self):
        token = UUID('00000000-0000-4000-8000-000000000001')
        routes = [
            ('/vket/status/', 'status_redirect', {}),
            ('/vket/auto-confirm/run/', 'auto_confirm_run', {}),
            (f'/vket/ack/{token}/', 'ack_notice', {'ack_token': token}),
            ('/vket/1/apply/', 'apply', {'pk': 1}),
            ('/vket/1/status/', 'status', {'pk': 1}),
            ('/vket/1/manage/', 'manage', {'pk': 1}),
            ('/vket/1/notices/', 'notice_list', {'pk': 1}),
            ('/vket/1/stage-copy/2/', 'stage_copy', {'pk': 1, 'participation_id': 2}),
        ]
        for url, name, kwargs in routes:
            with self.subTest(url=url):
                self.assertEqual(reverse(f'vket:{name}', kwargs=kwargs), url)
                self.assertEqual(resolve(url).view_name, f'vket:{name}')
                self.assertEqual(resolve(url).kwargs, kwargs)
        self.collaboration.slug = 'status'
        self.collaboration.save(update_fields=['slug'])
        response = self.client.get('/vket/status/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_detail_page_is_404_for_draft(self):
        client = Client()
        response = client.get(reverse('vket:detail', kwargs={'slug': self.draft_collaboration.slug}))
        self.assertEqual(response.status_code, 404)

    def test_collaboration_validation(self):
        today = timezone.localdate()
        collaboration = VketCollaboration(
            slug='invalid-collab',
            name='Invalid collaboration',
            period_start=today + timedelta(days=1),
            period_end=today,
            registration_deadline=today,
            lt_deadline=today - timedelta(days=1),
            phase=VketCollaboration.Phase.DRAFT,
        )
        with self.assertRaises(ValidationError):
            collaboration.full_clean()
