"""自分の発表一覧ページの表示を検証する。"""

from datetime import date, time

from django.test import TestCase, override_settings
from django.urls import reverse

from tests.factories import (
    make_community,
    make_event,
    make_event_detail,
    make_user,
)


@override_settings(DISCORD_AUTH_REQUIRED=False)
class MyPresentationsViewTests(TestCase):
    """承認済みの本人発表だけを専用ページに表示する。"""

    def setUp(self):
        self.user = make_user(
            user_name="my_presentations_user",
            email="my-presentations@example.com",
        )
        self.other_user = make_user(
            user_name="other_presentations_user",
            email="other-presentations@example.com",
        )
        self.community = make_community(name="発表テスト集会")
        self.event = make_event(self.community, event_date=date(2026, 8, 10))
        self.url = reverse("event:my_presentations")

    def test_requires_login(self):
        """未ログインユーザーはログイン画面へ遷移する。"""
        response = self.client.get(self.url)

        self.assertRedirects(
            response,
            f"{reverse('account:login')}?next={self.url}",
            fetch_redirect_response=False,
        )

    def test_shows_only_approved_presentations_for_current_user(self):
        """本人の承認済み発表だけを詳細・編集導線付きで表示する。"""
        approved_detail = make_event_detail(
            self.event,
            applicant=self.user,
            status="approved",
            theme="本人の承認済み発表",
        )
        make_event_detail(
            self.event,
            applicant=self.user,
            status="pending",
            theme="本人の承認待ち発表",
            start_time=time(22, 30),
        )
        make_event_detail(
            self.event,
            applicant=self.other_user,
            status="approved",
            theme="他人の承認済み発表",
            start_time=time(23, 0),
        )
        make_event_detail(
            self.event,
            applicant=self.user,
            status="approved",
            detail_type="SPECIAL",
            theme="本人の特別企画",
            start_time=time(23, 30),
        )
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertContains(response, "本人の承認済み発表")
        self.assertContains(
            response,
            reverse("event:detail", kwargs={"pk": approved_detail.pk}),
        )
        self.assertContains(
            response,
            reverse("event:detail_update", kwargs={"pk": approved_detail.pk}),
        )
        self.assertNotContains(response, "本人の承認待ち発表")
        self.assertNotContains(response, "他人の承認済み発表")
        self.assertNotContains(response, "本人の特別企画")
        self.assertEqual(list(response.context["presentations"]), [approved_detail])

    def test_shows_community_search_call_to_action_when_empty(self):
        """発表がない場合は集会一覧への申請導線を表示する。"""
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertContains(response, "集会を探して発表を申し込む")
        self.assertContains(response, reverse("community:list"))

    def test_orders_by_event_date_descending_then_start_time_ascending(self):
        """新しい開催日を先にし、同じ開催日は開始時刻順に表示する。"""
        newer_event = make_event(
            self.community,
            event_date=date(2026, 8, 12),
        )
        newer_detail = make_event_detail(
            newer_event,
            applicant=self.user,
            status="approved",
            theme="別日の新しい発表",
            start_time=time(23, 0),
        )
        later_detail = make_event_detail(
            self.event,
            applicant=self.user,
            status="approved",
            theme="同日の遅い発表",
            start_time=time(23, 30),
        )
        earlier_detail = make_event_detail(
            self.event,
            applicant=self.user,
            status="approved",
            theme="同日の早い発表",
            start_time=time(21, 0),
        )
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(
            list(response.context["presentations"]),
            [newer_detail, earlier_detail, later_detail],
        )

    def test_my_list_no_longer_includes_presentation_section_or_context(self):
        """集会管理ページは発表一覧を含まない。"""
        make_event_detail(
            self.event,
            applicant=self.user,
            status="approved",
            theme="専用ページだけに表示する発表",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("event:my_list"))

        self.assertNotContains(response, "speaker-presentations-heading")
        self.assertNotContains(response, "専用ページだけに表示する発表")
        self.assertContains(response, "イベントがありません")
        self.assertIsNone(response.context.get("speaker_event_details"))
