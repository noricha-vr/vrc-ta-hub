"""vket/views/public.py のクエリ数 (N+1) を検証するテスト.

CollaborationDetailView の context_data 構築時に、VketParticipation 件数を
増やしても SQL 発行数が一定（O(1)）に収まることを CaptureQueriesContext で
計測する。

参照: improve-loop W2 #15
"""

from __future__ import annotations

from datetime import timedelta

from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from community.models import Community
from event.models import Event, EventDetail
from vket.models import VketCollaboration, VketParticipation


# CollaborationDetailView が participation 件数によらず安定して
# 収まるべき SQL 発行数の上限。
# 計測実数（5 件時）は 3 クエリ。auth / セッション等のフレームワーク内部の
# キャッシュばらつきを許容しつつ、N+1 を確実に弾けるよう余裕を持って 15 と置く。
QUERY_COUNT_UPPER_BOUND = 15

# 1 件→10 件の増分許容値。
# select_related / prefetch_related が効いていれば差は数クエリ程度に収まる。
# auth / セッション系の揺らぎを吸収しつつ、N+1 の兆候（10 件で +10 クエリ）を
# 弾けるよう 3 を上限にする。
QUERY_COUNT_GROWTH_TOLERANCE = 3


class CollaborationDetailQueryCountTests(TestCase):
    """CollaborationDetailView の SQL 数が VketParticipation 件数に対して
    定数オーダーで収まることを検証する."""

    def setUp(self):
        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug="vket-2026-n-plus-1",
            name="N+1 検証用コラボ",
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.ENTRY_OPEN,
        )
        self.detail_url = reverse(
            "vket:detail", kwargs={"pk": self.collaboration.pk}
        )

    def _make_participation(self, idx: int) -> VketParticipation:
        """published_event と LT 詳細を伴う参加データを 1 件作る."""
        community = Community.objects.create(
            name=f"集会{idx}",
            status="approved",
            frequency="毎週",
        )
        # 件数ごとに時刻をずらして Event のユニーク制約に当てないようにする
        start_time = f"{20 + idx % 4:02d}:{(idx * 5) % 60:02d}"
        event = Event.objects.create(
            community=community,
            date=self.collaboration.period_start,
            start_time=start_time,
            duration=60,
        )
        EventDetail.objects.create(
            event=event,
            detail_type="LT",
            status="approved",
            speaker=f"登壇者{idx}",
            theme=f"テーマ{idx}",
            start_time=event.start_time,
            duration=15,
        )
        return VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=community,
            published_event=event,
        )

    def _measure_get(self, client: Client) -> int:
        """detail ページを 1 回 GET し、その間の SQL 発行数を返す."""
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_detail_query_count_is_bounded_with_five_participations(self):
        """参加 5 件で SQL 発行数が上限内に収まる."""
        for i in range(5):
            self._make_participation(i)

        client = Client()
        query_count = self._measure_get(client)

        self.assertLessEqual(
            query_count,
            QUERY_COUNT_UPPER_BOUND,
            msg=(
                f"参加 5 件時の SQL 数 {query_count} が上限 "
                f"{QUERY_COUNT_UPPER_BOUND} を超過。N+1 が混入した可能性がある。"
            ),
        )

    def test_detail_query_count_does_not_scale_with_participations(self):
        """参加件数を 1 → 10 に増やしても SQL 数の増分が小さい（N+1 不在）."""
        client = Client()

        # まず 1 件で計測
        self._make_participation(0)
        count_one = self._measure_get(client)

        # 9 件追加して合計 10 件にしてから再計測
        for i in range(1, 10):
            self._make_participation(i)
        count_ten = self._measure_get(client)

        diff = count_ten - count_one
        self.assertLessEqual(
            diff,
            QUERY_COUNT_GROWTH_TOLERANCE,
            msg=(
                f"参加件数を 1 → 10 に増やして SQL 数が "
                f"{count_one} → {count_ten} (diff={diff}) に増加。"
                "N+1 が残っている可能性がある。"
            ),
        )
