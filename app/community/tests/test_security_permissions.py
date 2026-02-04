"""コミュニティ関連の権限テスト（セキュリティ）."""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from community.models import Community


User = get_user_model()


class WaitingListSuperuserOnlyTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            user_name="normal_user",
            email="normal@example.com",
            password="testpass123",
        )
        self.superuser = User.objects.create_superuser(
            user_name="admin_user",
            email="admin@example.com",
            password="adminpass123",
        )

    def test_waiting_list_is_forbidden_for_non_superuser(self):
        self.client.login(username="normal_user", password="testpass123")
        response = self.client.get(reverse("community:waiting_list"))
        self.assertEqual(response.status_code, 403)

    def test_waiting_list_is_allowed_for_superuser(self):
        self.client.login(username="admin_user", password="adminpass123")
        response = self.client.get(reverse("community:waiting_list"))
        self.assertEqual(response.status_code, 200)


class PendingCommunityDetailSuperuserOnlyTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            user_name="normal_user2",
            email="normal2@example.com",
            password="testpass123",
        )
        self.superuser = User.objects.create_superuser(
            user_name="admin_user2",
            email="admin2@example.com",
            password="adminpass123",
        )
        self.pending_community = Community.objects.create(
            name="Pending Community",
            status="pending",
            frequency="毎週",
            organizers="Org",
        )

    def test_pending_community_detail_is_hidden_from_non_superuser(self):
        self.client.login(username="normal_user2", password="testpass123")
        response = self.client.get(
            reverse("community:detail", kwargs={"pk": self.pending_community.pk})
        )
        self.assertEqual(response.status_code, 404)

    def test_pending_community_detail_is_visible_to_superuser(self):
        self.client.login(username="admin_user2", password="adminpass123")
        response = self.client.get(
            reverse("community:detail", kwargs={"pk": self.pending_community.pk})
        )
        self.assertEqual(response.status_code, 200)

