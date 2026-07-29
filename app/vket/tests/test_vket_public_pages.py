"""Vketコラボ機能のテスト."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from vket.models import (
    VketCollaboration,
)


User = get_user_model()


class VketPublicPagesTests(TestCase):
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
        # 下書きは表示されない
        self.assertNotContains(response, self.draft_collaboration.name)

    def test_detail_page(self):
        client = Client()
        response = client.get(reverse('vket:detail', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.collaboration.name)

    def test_detail_page_is_404_for_draft(self):
        client = Client()
        response = client.get(reverse('vket:detail', kwargs={'pk': self.draft_collaboration.pk}))
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
