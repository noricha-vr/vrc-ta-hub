"""EventDetail（Web UI）の権限テスト."""

from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from community.models import Community, CommunityMember
from event.models import Event, EventDetail


User = get_user_model()


class EventDetailPermissionTests(TestCase):
    """EventDetailの作成/更新/削除がコミュニティ管理者に限定されることを確認する."""

    def setUp(self):
        self.client = Client()

        self.owner = User.objects.create_user(
            user_name="owner_user",
            email="owner@example.com",
            password="testpass123",
        )
        self.other_user = User.objects.create_user(
            user_name="other_user",
            email="other@example.com",
            password="testpass123",
        )

        self.community = Community.objects.create(
            name="Test Community",
            status="approved",
            frequency="毎週",
            organizers="Test Organizer",
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER,
        )

        self.event = Event.objects.create(
            community=self.community,
            date=date(2026, 2, 10),
            start_time=time(22, 0),
            duration=60,
            weekday="Tue",
        )
        self.event_detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            start_time=time(22, 0),
            duration=30,
            speaker="Speaker",
            theme="Theme",
            contents="contents",
        )

    def test_non_member_cannot_access_event_detail_create_view(self):
        """非メンバーはEventDetail作成ページにアクセスできない（403）."""
        self.client.login(username="other_user", password="testpass123")

        url = reverse("event:detail_create", kwargs={"event_pk": self.event.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 403)

    def test_non_member_cannot_access_event_detail_update_view(self):
        """非メンバーはEventDetail更新ページにアクセスできない（403）."""
        self.client.login(username="other_user", password="testpass123")

        url = reverse("event:detail_update", kwargs={"pk": self.event_detail.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 403)

    def test_non_member_cannot_delete_event_detail(self):
        """非メンバーはEventDetailを削除できない（403）."""
        self.client.login(username="other_user", password="testpass123")

        url = reverse("event:detail_delete", kwargs={"pk": self.event_detail.pk})
        response = self.client.post(url)

        self.assertEqual(response.status_code, 403)
        self.assertTrue(EventDetail.objects.filter(pk=self.event_detail.pk).exists())

