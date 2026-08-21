"""my_list の発表者アカウント紐づけ導線を検証する。"""
from django.test import Client, TestCase
from django.urls import reverse

from tests.factories import make_community, make_event, make_event_detail, make_user


class EventMyListSpeakerLinkTest(TestCase):
    """既存の記事ページ内招待リンク発行UIへの導線を検証する。"""

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user(
            user_name="speaker_link_owner",
            email="speaker-link-owner@example.com",
        )
        cls.speaker = make_user(
            user_name="linked_speaker",
            email="linked-speaker@example.com",
        )
        cls.community = make_community(
            name="紐づけ導線テスト集会",
            owner=cls.owner,
        )
        cls.event = make_event(cls.community)
        cls.unlinked_detail = make_event_detail(
            cls.event,
            status="approved",
            theme="未紐づけの発表",
        )
        cls.linked_detail = make_event_detail(
            cls.event,
            applicant=cls.speaker,
            status="approved",
            theme="紐づけ済みの発表",
        )
        cls.pending_detail = make_event_detail(
            cls.event,
            status="pending",
            theme="承認待ちの発表",
        )
        cls.special_detail = make_event_detail(
            cls.event,
            detail_type="SPECIAL",
            status="approved",
            theme="特別企画",
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.owner)

    def test_unlinked_approved_lt_links_to_article_invite_section(self):
        """未紐づけの承認済み発表は記事内の招待リンク発行UIへ遷移できる。"""
        response = self.client.get(reverse("event:my_list"))

        target = (
            reverse("event:detail", kwargs={"pk": self.unlinked_detail.pk})
            + "#speaker-account-heading"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{target}"')

    def test_linked_lt_does_not_show_invite_section_link(self):
        """紐づけ済みの発表には招待リンク発行UIへの導線を表示しない。"""
        response = self.client.get(reverse("event:my_list"))

        target = (
            reverse("event:detail", kwargs={"pk": self.linked_detail.pk})
            + "#speaker-account-heading"
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'href="{target}"')

    def test_pending_lt_does_not_link_to_unavailable_invite_section(self):
        """承認待ちでは記事側に存在しない招待リンク発行UIへ誘導しない。"""
        response = self.client.get(reverse("event:my_list"))

        target = (
            reverse("event:detail", kwargs={"pk": self.pending_detail.pk})
            + "#speaker-account-heading"
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'href="{target}"')

    def test_unlinked_special_does_not_link_to_unavailable_invite_section(self):
        """特別企画では記事側に存在しない招待リンク発行UIへ誘導しない。"""
        response = self.client.get(reverse("event:my_list"))

        target = (
            reverse("event:detail", kwargs={"pk": self.special_detail.pk})
            + "#speaker-account-heading"
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'href="{target}"')
