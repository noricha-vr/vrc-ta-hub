"""行ロックを提供する DB で同時呼出しを検証する（SQLite ではスキップ）。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import time, timedelta
from threading import Barrier

from django.db import close_old_connections, connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from event.models import EventDetail
from tests.factories import make_community
from vket.auto_confirm import auto_confirm_schedules
from vket.models import VketCollaboration, VketParticipation, VketPresentation


class VketAutoConfirmConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_runs_confirm_and_publish_only_once(self):
        """二つの接続から同時に走らせても、確定と公開詳細の作成は一回だけ。"""
        today = timezone.localdate()
        collaboration = VketCollaboration.objects.create(
            slug='auto-confirm-concurrent', name='コラボ', phase=VketCollaboration.Phase.ENTRY_OPEN,
            period_start=today, period_end=today + timedelta(days=7),
            registration_deadline=today, lt_deadline=today,
        )
        participation = VketParticipation.objects.create(
            collaboration=collaboration, community=make_community(name='同時確定集会'),
            requested_date=today, requested_start_time=time(21), requested_duration=60,
            progress=VketParticipation.Progress.APPLIED, applied_at=timezone.now(),
        )
        presentation = VketPresentation.objects.create(
            participation=participation, speaker='登壇者', theme='テーマ',
            requested_start_time=time(21), duration=30,
        )
        barrier = Barrier(2)

        def run():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return auto_confirm_schedules()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(result['confirmed'] for result in results), [0, 1])
        participation.refresh_from_db()
        presentation.refresh_from_db()
        self.assertEqual(participation.confirmed_start_time, time(21))
        self.assertEqual(presentation.confirmed_start_time, time(21))
        self.assertEqual(EventDetail.objects.filter(event=participation.published_event).count(), 1)
