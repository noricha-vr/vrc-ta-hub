"""TwitterTemplate削除の権限テスト."""

import datetime

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from community.models import Community, CommunityMember
from twitter.models import TwitterTemplate


CustomUser = get_user_model()


class TwitterTemplateDeletePermissionTest(TestCase):
    def setUp(self):
        self.client = Client()

        self.owner = CustomUser.objects.create_user(
            user_name="owner_user_del",
            email="owner_del@example.com",
            password="testpassword",
        )
        self.other_user = CustomUser.objects.create_user(
            user_name="other_user_del",
            email="other_del@example.com",
            password="testpassword",
        )

        self.community = Community.objects.create(
            name="Delete Perm Community",
            start_time=datetime.time(21, 0),
            duration=60,
            weekdays=["Mon"],
            frequency="Weekly",
            organizers="Org",
            description="Desc",
            platform="All",
            status="approved",
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER,
        )

        self.template = TwitterTemplate.objects.create(
            community=self.community,
            name="Template to delete",
            template="Test tweet content",
        )

    def test_non_member_cannot_delete_template(self):
        self.client.login(username="other_user_del", password="testpassword")

        url = reverse("twitter:template_delete", kwargs={"pk": self.template.pk})
        response = self.client.post(url)

        self.assertEqual(response.status_code, 403)
        self.assertTrue(TwitterTemplate.objects.filter(pk=self.template.pk).exists())

