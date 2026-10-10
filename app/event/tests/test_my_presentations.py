"""発表一覧の本人絞り込みと旧URLの互換性を検証する。"""

from datetime import date, time

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tests.factories import (
    make_community,
    make_event,
    make_event_detail,
    make_user,
)


@override_settings(DISCORD_AUTH_REQUIRED=False)
class MyPresentationsViewTests(TestCase):
    """承認済みの本人発表を統合された一覧に表示する。"""

    def setUp(self):
        cache.clear()
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
        self.legacy_url = reverse("event:my_presentations")
        self.history_url = reverse("event:detail_history")
        self.url = f"{self.history_url}?mine=1"

    def test_requires_login(self):
        """未ログインユーザーはログイン画面へ遷移する。"""
        response = self.client.get(self.legacy_url)

        self.assertRedirects(
            response,
            f"{reverse('account:login')}?next={self.legacy_url}",
            fetch_redirect_response=False,
        )

    def test_legacy_url_redirects_to_mine_filter(self):
        """旧URLはログイン後に本人絞り込みへ遷移する。"""
        self.client.force_login(self.user)

        self.assertRedirects(self.client.get(self.legacy_url), self.url)

    def test_mine_includes_presentations_with_and_without_materials(self):
        """本人の承認済み発表は資料の有無によらず表示し、件数も一致する。"""
        without_materials = make_event_detail(
            self.event, applicant=self.user, status="approved", theme="資料なし発表",
        )
        with_materials = make_event_detail(
            self.event, applicant=self.user, status="approved", theme="資料あり発表",
            slide_url="https://example.com/slides", start_time=time(23, 0),
        )
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertCountEqual(
            response.context["event_details"], [without_materials, with_materials],
        )
        self.assertEqual(response.context["all_count"], 2)
        self.assertEqual(response.context["materials_count"], 1)
        self.assertTrue(response.context["show_all"])
        self.assertContains(response, "全 2 件")
        self.assertContains(response, "すべて (2)")
        self.assertNotContains(response, "資料あり (1)")

    def test_anonymous_mine_is_ignored(self):
        """未ログイン時の本人絞り込みは無視し、資料ありの通常一覧を表示する。"""
        visible = make_event_detail(
            self.event, applicant=self.other_user, status="approved", contents="本文あり",
        )
        make_event_detail(self.event, applicant=self.user, status="approved", theme="資料なし")

        normal_response = self.client.get(self.history_url)
        response = self.client.get(self.url)

        self.assertEqual(list(response.context["event_details"]), [visible])
        self.assertEqual(
            list(response.context["event_details"]), list(normal_response.context["event_details"]),
        )
        self.assertFalse(response.context["is_mine"])
        self.assertFalse(response.context["show_all"])
        self.assertNotContains(response, "自分の発表")
        self.assertNotIn("mine", response.context["current_query_params"])

    def test_edit_links_only_appear_for_current_applicant(self):
        """資料ありカードと資料なし行の両方で、本人にだけ編集リンクを出す。"""
        self.client.force_login(self.user)
        for applicant in (self.user, self.other_user, None):
            for contents in ("", "本文あり"):
                detail = make_event_detail(
                    self.event, applicant=applicant, status="approved", contents=contents,
                )
                response = self.client.get(self.history_url, {"view": "all"})
                edit_url = reverse("event:detail_update", kwargs={"pk": detail.pk})
                if applicant == self.user:
                    self.assertContains(response, f'href="{edit_url}"', count=1)
                else:
                    self.assertNotContains(response, f'href="{edit_url}"')

        self.client.logout()
        response = self.client.get(self.history_url, {"view": "all"})
        self.assertNotContains(response, "bi-pencil me-1")

    def test_edit_link_hidden_for_own_non_lt_details(self):
        """本人の申請でも、編集画面が許さない特別企画・ブログには編集リンクを出さない。"""
        self.client.force_login(self.user)
        for detail_type in ("SPECIAL", "BLOG"):
            detail = make_event_detail(
                self.event, applicant=self.user, status="approved", detail_type=detail_type,
                contents="本文あり",
            )
            response = self.client.get(self.history_url, {"view": "all", "type": "special"})
            edit_url = reverse("event:detail_update", kwargs={"pk": detail.pk})
            self.assertNotContains(response, f'href="{edit_url}"')

    def test_mine_search_without_match_says_no_match(self):
        """自分の発表を検索して当たらない時は「該当する発表はありません」と出す。"""
        self.client.force_login(self.user)
        make_event_detail(self.event, applicant=self.user, status="approved", theme="当たらない")
        response = self.client.get(self.history_url, {"mine": "1", "q": "存在しない語"})
        self.assertContains(response, "該当する発表はありません。")
        self.assertNotContains(response, "編集できる発表はまだありません。")

    def test_mine_preserves_previous_visibility_and_ignores_special_type(self):
        """従来どおり集会の承認状態に依存せず、特別企画へ切り替わらない。"""
        pending_community = make_community(name="承認待ち集会", status="pending")
        detail = make_event_detail(
            make_event(pending_community, event_date=date(2026, 8, 11)),
            applicant=self.user, status="approved",
        )
        make_event_detail(
            self.event, applicant=self.user, status="approved", detail_type="BLOG",
        )
        make_event_detail(self.event, applicant=self.user, status="rejected")
        self.client.force_login(self.user)

        response = self.client.get(self.history_url, {"mine": "1", "type": "special"})

        self.assertEqual(list(response.context["event_details"]), [detail])
        self.assertFalse(response.context["is_special"])
        self.assertNotIn("type", response.context["current_query_params"])

    def test_mine_chip_and_search_preserve_other_filters(self):
        """検索フォームと各リンクは本人絞り込みを保持し、チップで解除できる。"""
        make_event_detail(
            self.event, applicant=self.user, status="approved", theme="対象テーマ", speaker="本人",
        )
        self.client.force_login(self.user)

        response = self.client.get(self.history_url, {"mine": "1", "q": "対象", "speaker": "本人"})

        self.assertContains(response, '<input type="hidden" name="mine" value="1">')
        chip = next(chip for chip in response.context["filter_chips"] if chip["label"] == "自分の発表")
        self.assertNotIn("mine=1", chip["remove_url"])
        self.assertIn("q=", chip["remove_url"])
        self.assertIn("speaker=", chip["remove_url"])
        self.assertEqual(response.context["clear_filters_url"], self.history_url)
        self.assertIn("mine=1", response.context["speaker_link_base_query"])
        self.assertIn("mine=1", response.context["community_link_base_query"])
        self.assertNotIn("mine=1", response.context["type_special_url"])

    def test_invalid_mine_value_uses_normal_list(self):
        """既知の値以外は本人絞り込みとして扱わない。"""
        make_event_detail(self.event, applicant=self.user, status="approved")
        self.client.force_login(self.user)

        response = self.client.get(self.history_url, {"mine": "other"})

        self.assertFalse(response.context["is_mine"])
        self.assertEqual(list(response.context["event_details"]), [])
        self.assertNotIn("mine", response.context["current_query_params"])

    def test_edit_links_do_not_add_queries_per_presentation(self):
        """カードの本人判定と登録状況表示は発表数に比例したクエリを発行しない。"""
        make_event_detail(self.event, applicant=self.user, status="approved", contents="本文あり")
        self.client.force_login(self.user)
        self.client.get(self.url)

        with CaptureQueriesContext(connection) as mine_queries:
            self.client.get(self.url)
        with CaptureQueriesContext(connection) as normal_queries:
            self.client.get(self.history_url)
        self.assertEqual(len(mine_queries), len(normal_queries))

        for index in range(5):
            make_event_detail(
                self.event, applicant=self.user, status="approved", contents="本文あり",
                theme=f"追加発表{index}",
            )
        with CaptureQueriesContext(connection) as additional_queries:
            self.client.get(self.url)
        self.assertEqual(len(additional_queries), len(mine_queries))

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
        self.assertEqual(list(response.context["event_details"]), [approved_detail])

    def test_shows_community_search_call_to_action_when_empty(self):
        """発表がない場合は集会一覧への申請導線を表示する。"""
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertContains(response, "集会を探して発表を申し込む")
        self.assertContains(response, reverse("community:list"))

    def test_shows_youtube_thumbnail_when_thumbnail_image_is_missing(self):
        """サムネイル画像が無い発表は YouTube のサムネイルにフォールバックする。"""
        make_event_detail(
            self.event,
            applicant=self.user,
            status="approved",
            theme="動画あり発表",
            youtube_url="https://www.youtube.com/watch?v=abcdefghijk",
        )
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertContains(
            response,
            "https://img.youtube.com/vi/abcdefghijk/mqdefault.jpg",
        )

    def test_shows_placeholder_when_no_image_is_available(self):
        """画像が一切無い発表はプレースホルダーアイコンを表示する。"""
        make_event_detail(
            self.event,
            applicant=self.user,
            status="approved",
            theme="画像なし発表",
        )
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertContains(response, "bi-image")
        self.assertNotContains(response, "img.youtube.com")

    def test_shows_material_status_badges(self):
        """スライド・動画・記事の未登録状況を本人のカードに表示する。"""
        make_event_detail(
            self.event,
            applicant=self.user,
            status="approved",
            theme="資料なし発表",
        )
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertContains(response, "スライド未登録")
        self.assertContains(response, "動画未登録")
        self.assertContains(response, "記事未生成")
        self.assertNotContains(response, "bi-eye")

    def test_shows_registered_badges_when_materials_exist(self):
        """資料が登録済みの発表は登録済みバッジを表示する。"""
        make_event_detail(
            self.event,
            applicant=self.user,
            status="approved",
            theme="資料あり発表",
            slide_url="https://example.com/slides",
            youtube_url="https://www.youtube.com/watch?v=abcdefghijk",
            contents="本文あり",
        )
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertNotContains(response, "スライド未登録")
        self.assertNotContains(response, "動画未登録")
        self.assertNotContains(response, "記事未生成")
        self.assertContains(response, "全 1 件")

    def test_paginates_by_twenty(self):
        """21 件以上は 20 件ずつページ分割する。"""
        for index in range(21):
            make_event_detail(
                self.event,
                applicant=self.user,
                status="approved",
                theme=f"発表{index:02d}",
                start_time=time(hour=index),
            )
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(len(response.context["event_details"]), 20)
        self.assertTrue(response.context["page_obj"].has_next())
        self.assertContains(response, "全 21 件")
        self.assertContains(response, "?page=2&mine=1")

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
            list(response.context["event_details"]),
            [newer_detail, earlier_detail, later_detail],
        )

    def test_my_list_no_longer_includes_presentation_section_or_context(self):
        """集会管理ページは発表一覧を含まない。"""
        make_event_detail(
            self.event,
            applicant=self.user,
            status="approved",
            theme="本人絞り込みに表示する発表",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("event:my_list"))

        self.assertNotContains(response, "speaker-presentations-heading")
        self.assertNotContains(response, "本人絞り込みに表示する発表")
        self.assertContains(response, "イベントがありません")
        self.assertIsNone(response.context.get("speaker_event_details"))
