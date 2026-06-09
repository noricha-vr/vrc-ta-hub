"""EventDetail の soft delete 機能のテスト.

`deleted_at` カラム + `objects` / `all_objects` マネージャの組み合わせで:

- 既存の `EventDetail.objects.filter(...)` は生存レコードのみ返す（後方互換）
- インスタンス `.delete()` は soft delete として動作する
- QuerySet `.delete()` も soft delete として動作する
- `all_objects` は削除済みを含めた全件を返す
- `restore()` で削除を取り消せる
- 関連オブジェクト（参照）が orphan にならない
"""
from datetime import date, time

from django.test import TestCase
from django.utils import timezone

from community.models import Community
from event.models import Event, EventDetail


class EventDetailSoftDeleteTests(TestCase):
    """EventDetail soft delete の中核挙動を検証する."""

    @classmethod
    def setUpTestData(cls):
        cls.community = Community.objects.create(
            name='Soft Delete Test Community',
            status='approved',
            frequency='毎週',
            organizers='Test Organizer',
        )
        cls.event = Event.objects.create(
            community=cls.community,
            date=date(2026, 6, 10),
            start_time=time(22, 0),
            duration=60,
            weekday='Wed',
        )

    def _create_detail(self, theme: str = 'Test Theme') -> EventDetail:
        return EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            start_time=time(22, 0),
            duration=15,
            speaker='Test Speaker',
            theme=theme,
            status='approved',
        )

    def test_instance_delete_marks_deleted_at(self):
        """instance.delete() で deleted_at がセットされ、row は残る."""
        detail = self._create_detail()
        pk = detail.pk

        detail.delete()

        # `objects` からは消える（生存のみ）
        self.assertFalse(EventDetail.objects.filter(pk=pk).exists())
        # `all_objects` からは引ける（物理的には残っている）
        self.assertTrue(EventDetail.all_objects.filter(pk=pk).exists())

        reloaded = EventDetail.all_objects.get(pk=pk)
        self.assertIsNotNone(reloaded.deleted_at)

    def test_queryset_delete_is_soft(self):
        """queryset.delete() も soft delete として動作する."""
        d1 = self._create_detail(theme='QS Theme 1')
        d2 = self._create_detail(theme='QS Theme 2')
        pks = [d1.pk, d2.pk]

        EventDetail.objects.filter(pk__in=pks).delete()

        self.assertEqual(EventDetail.objects.filter(pk__in=pks).count(), 0)
        self.assertEqual(EventDetail.all_objects.filter(pk__in=pks).count(), 2)
        for reloaded in EventDetail.all_objects.filter(pk__in=pks):
            self.assertIsNotNone(reloaded.deleted_at)

    def test_restore_undoes_soft_delete(self):
        """instance.restore() で生存状態に戻る."""
        detail = self._create_detail(theme='Restorable')
        pk = detail.pk

        detail.delete()
        self.assertFalse(EventDetail.objects.filter(pk=pk).exists())

        detail.refresh_from_db()
        detail.restore()

        self.assertTrue(EventDetail.objects.filter(pk=pk).exists())
        detail.refresh_from_db()
        self.assertIsNone(detail.deleted_at)

    def test_objects_count_excludes_deleted(self):
        """objects.count() は削除済みを含まない."""
        d1 = self._create_detail(theme='Counted 1')
        d2 = self._create_detail(theme='Counted 2')
        d3 = self._create_detail(theme='Counted 3')

        d2.delete()

        # 生存: d1, d3 / 削除: d2
        self.assertEqual(EventDetail.objects.filter(event=self.event).count(), 2)
        self.assertEqual(EventDetail.all_objects.filter(event=self.event).count(), 3)

        live_pks = set(
            EventDetail.objects.filter(event=self.event).values_list('pk', flat=True)
        )
        self.assertIn(d1.pk, live_pks)
        self.assertIn(d3.pk, live_pks)
        self.assertNotIn(d2.pk, live_pks)

    def test_hard_delete_actually_removes_row(self):
        """hard_delete() は DB から row を消す（復元不可）."""
        detail = self._create_detail(theme='Will Be Hard Deleted')
        pk = detail.pk

        detail.hard_delete()

        self.assertFalse(EventDetail.objects.filter(pk=pk).exists())
        self.assertFalse(EventDetail.all_objects.filter(pk=pk).exists())

    def test_queryset_hard_delete_actually_removes_rows(self):
        """queryset.hard_delete() は DB から row を消す."""
        d1 = self._create_detail(theme='QS Hard 1')
        d2 = self._create_detail(theme='QS Hard 2')
        pks = [d1.pk, d2.pk]

        EventDetail.all_objects.filter(pk__in=pks).hard_delete()

        self.assertEqual(EventDetail.all_objects.filter(pk__in=pks).count(), 0)

    def test_deleted_at_is_timezone_aware_now(self):
        """deleted_at は呼び出し時刻に近い aware datetime."""
        detail = self._create_detail(theme='Timestamped')

        before = timezone.now()
        detail.delete()
        after = timezone.now()

        detail.refresh_from_db()
        self.assertIsNotNone(detail.deleted_at)
        self.assertGreaterEqual(detail.deleted_at, before)
        self.assertLessEqual(detail.deleted_at, after)

    def test_default_manager_returns_only_live(self):
        """`_default_manager` (= objects) も生存のみ返す."""
        # 一部のジェネリックなコード（例: serializer / 管理画面の自動補完）は
        # `_default_manager` を介してアクセスするため、後方互換の意味で
        # 既存の `objects` と同じ挙動になっていることを確認する。
        d1 = self._create_detail(theme='Default 1')
        d2 = self._create_detail(theme='Default 2')
        d2.delete()

        default_pks = set(
            EventDetail._default_manager.filter(event=self.event).values_list('pk', flat=True)
        )
        self.assertEqual(default_pks, {d1.pk})

    def test_restore_via_queryset(self):
        """queryset.restore() で複数件まとめて復元できる."""
        d1 = self._create_detail(theme='Bulk Restore 1')
        d2 = self._create_detail(theme='Bulk Restore 2')

        EventDetail.objects.filter(pk__in=[d1.pk, d2.pk]).delete()
        self.assertEqual(EventDetail.objects.filter(pk__in=[d1.pk, d2.pk]).count(), 0)

        EventDetail.all_objects.filter(pk__in=[d1.pk, d2.pk]).restore()

        self.assertEqual(EventDetail.objects.filter(pk__in=[d1.pk, d2.pk]).count(), 2)

    def test_soft_delete_emits_post_delete_signal(self):
        """soft_delete() は既存の post_delete ハンドラを発火させる.

        UI からは「消えた」ように見えるので、キャッシュ無効化等が
        post_delete に依存しているコード（ta_hub / twitter signals）は
        soft delete でも追従する必要がある。
        """
        from django.db.models.signals import post_delete

        detail = self._create_detail(theme='Signal Test')

        received = []

        def _handler(sender, instance, **kwargs):
            received.append(instance.pk)

        post_delete.connect(_handler, sender=EventDetail)
        try:
            detail.delete()
        finally:
            post_delete.disconnect(_handler, sender=EventDetail)

        self.assertEqual(received, [detail.pk])
