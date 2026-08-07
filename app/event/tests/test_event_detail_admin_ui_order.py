"""イベント詳細ページの管理者向けブロックの表示順・レイアウトのテスト.

集会オーナーが見た時に「管理操作 → 発表の注意書き → 発表者アカウント →
アクセス解析」の順で並ぶこと、および発表者アカウントの紐づけ済み表示が
1行にまとまることを、レンダリング結果の HTML で検証する（Issue #619）。
"""
import re
from datetime import date, time, timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from analytics.models import PageAnalytics
from tests.factories import make_community, make_event, make_event_detail, make_user


class EventDetailAdminUiOrderTests(TestCase):
    """集会オーナー視点での管理ブロックの並び順とレイアウト."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user(user_name='ui_owner', email='ui_owner@example.com')
        cls.speaker = make_user(user_name='ui_speaker', email='ui_speaker@example.com')
        cls.community = make_community(name='表示順検証集会', owner=cls.owner)
        cls.event = make_event(
            cls.community,
            event_date=date(2026, 2, 10),
            start_time=time(22, 0),
            weekday='Tue',
        )
        cls.linked_detail = make_event_detail(
            cls.event,
            applicant=cls.speaker,
            status='approved',
            theme='紐づけ済みの発表',
        )
        # アクセス解析ブロックの描画には集計データが要る。集計対象は前日まで
        yesterday = timezone.localdate() - timedelta(days=1)
        PageAnalytics.objects.create(
            page_path=f'/event/detail/{cls.linked_detail.pk}/', date=yesterday,
            content_type=PageAnalytics.ContentType.EVENT_DETAIL,
            community=cls.community, object_id=cls.linked_detail.pk,
            pv=42, users=30, sessions=35, source_medium='ui-order / organic',
        )

    def _owner_html(self, detail) -> str:
        client = Client()
        client.force_login(self.owner)
        response = client.get(reverse('event:detail', kwargs={'pk': detail.pk}))
        self.assertEqual(response.status_code, 200)
        return response.content.decode('utf-8')

    def test_admin_blocks_render_in_specified_order(self):
        """管理操作・注意書き・発表者アカウント・アクセス解析がこの順に出る."""
        html = self._owner_html(self.linked_detail)

        positions = [
            html.index('id="admin-actions"'),
            html.index('id="lt-article-notes"'),
            html.index('id="speaker-account-heading"'),
            html.index('id="analytics-daily-chart"'),
        ]

        self.assertEqual(positions, sorted(positions))

    def test_linked_speaker_account_groups_heading_and_unlink_in_one_row(self):
        """紐づけ済み表示は見出し・状態文・解除ボタンを1行にまとめる."""
        html = self._owner_html(self.linked_detail)

        row = re.search(
            r'<div class="speaker-account-linked[^"]*">(.*?)</div>', html, re.DOTALL
        )
        if row is None:
            self.fail('speaker-account-linked の行が描画されていない')

        self.assertIn('id="speaker-account-heading"', row.group(1))
        self.assertIn(self.speaker.display_label, row.group(1))
        self.assertIn('data-bs-target="#speaker-unlink-modal"', row.group(1))

    def test_unlinked_speaker_account_keeps_invite_form(self):
        """未紐づけの発表では招待リンク発行UIが従来どおり出る."""
        unlinked_detail = make_event_detail(
            self.event, status='approved', theme='未紐づけの発表',
        )

        html = self._owner_html(unlinked_detail)

        self.assertIn('id="speaker-invite-form"', html)
        self.assertIn('id="speaker-invite-url"', html)
        # class="..." で見る（同名の CSS セレクタが <style> に出るため素の語では判定できない）
        self.assertNotIn('class="speaker-account-linked', html)
