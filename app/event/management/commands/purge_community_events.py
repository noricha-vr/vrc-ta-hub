from datetime import date, datetime, timedelta
from typing import Iterable

from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError

from event.google_calendar import GoogleCalendarService
from event.models import Event, RecurrenceRule
from website.settings import GOOGLE_CALENDAR_CREDENTIALS, GOOGLE_CALENDAR_ID


class Command(BaseCommand):
    help = (
        "指定コミュニティの指定日以降イベントをDBとGoogleカレンダーから削除します。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--community",
            action="append",
            required=True,
            help="削除対象コミュニティ名（複数指定可）",
        )
        parser.add_argument(
            "--from-date",
            required=True,
            help="削除開始日（YYYY-MM-DD）",
        )
        parser.add_argument(
            "--delete-rules",
            action="store_true",
            help="対象コミュニティの定期ルールも停止（未来イベント削除後にルール削除）",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="実際には削除せず、対象件数のみ表示",
        )
        parser.add_argument(
            "--google-window-days",
            type=int,
            default=180,
            help="Googleカレンダー走査の分割日数（デフォルト: 180日）",
        )
        parser.add_argument(
            "--google-years",
            type=int,
            default=5,
            help="Googleカレンダー走査年数（from-date起点、デフォルト: 5年）",
        )

    def handle(self, *args, **options):
        community_names = sorted(set(options["community"]))
        from_date = self._parse_date(options["from_date"])
        dry_run = options["dry_run"]
        delete_rules = options["delete_rules"]
        window_days = options["google_window_days"]
        google_years = options["google_years"]

        if window_days <= 0:
            raise CommandError("--google-window-days は1以上を指定してください。")
        if google_years <= 0:
            raise CommandError("--google-years は1以上を指定してください。")

        self.stdout.write(self.style.WARNING("=" * 80))
        self.stdout.write(self.style.WARNING("イベント削除タスク"))
        self.stdout.write(self.style.WARNING(f"コミュニティ: {', '.join(community_names)}"))
        self.stdout.write(self.style.WARNING(f"削除開始日: {from_date}"))
        self.stdout.write(self.style.WARNING(f"定期ルール停止: {delete_rules}"))
        self.stdout.write(self.style.WARNING(f"DRY-RUN: {dry_run}"))
        self.stdout.write(self.style.WARNING("=" * 80))

        db_deleted = self._purge_database_events(
            community_names=community_names,
            from_date=from_date,
            dry_run=dry_run,
        )

        rules_deleted = self._purge_recurrence_rules(
            community_names=community_names,
            from_date=from_date,
            delete_rules=delete_rules,
            dry_run=dry_run,
        )

        google_deleted = self._purge_google_calendar_events(
            community_names=community_names,
            from_date=from_date,
            dry_run=dry_run,
            window_days=window_days,
            google_years=google_years,
        )

        if not dry_run:
            cache.clear()
            self.stdout.write(self.style.SUCCESS("キャッシュをクリアしました。"))

        self.stdout.write(self.style.SUCCESS("=" * 80))
        self.stdout.write(
            self.style.SUCCESS(
                f"完了: DBイベント={db_deleted}件, 定期ルール={rules_deleted}件, Googleイベント={google_deleted}件"
            )
        )
        self.stdout.write(self.style.SUCCESS("=" * 80))

    def _parse_date(self, value: str) -> date:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise CommandError(f"日付形式が不正です: {value} (期待: YYYY-MM-DD)") from exc

    def _purge_database_events(self, community_names: Iterable[str], from_date: date, dry_run: bool) -> int:
        qs = Event.objects.filter(
            community__name__in=community_names,
            date__gte=from_date,
        ).select_related("community")
        event_ids = list(qs.values_list("id", flat=True))

        self.stdout.write(f"DB対象イベント件数: {len(event_ids)}件")
        if not event_ids or dry_run:
            return len(event_ids)

        deleted_count, _ = Event.objects.filter(id__in=event_ids).delete()
        self.stdout.write(self.style.SUCCESS(f"DB削除実行: {deleted_count}件（関連オブジェクト含む）"))
        return len(event_ids)

    def _purge_recurrence_rules(
        self,
        community_names: Iterable[str],
        from_date: date,
        delete_rules: bool,
        dry_run: bool,
    ) -> int:
        if not delete_rules:
            self.stdout.write("定期ルール停止: スキップ")
            return 0

        rules = list(
            RecurrenceRule.objects.filter(community__name__in=community_names).select_related("community")
        )
        self.stdout.write(f"対象定期ルール件数: {len(rules)}件")

        if dry_run:
            return len(rules)

        deleted_rules = 0
        for rule in rules:
            deleted_events = rule.delete_future_events(from_date)
            rule.delete(delete_future_events=False)
            deleted_rules += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"定期ルール削除: community={rule.community.name if rule.community else '未設定'} "
                    f"future_events={deleted_events}"
                )
            )
        return deleted_rules

    def _purge_google_calendar_events(
        self,
        community_names: Iterable[str],
        from_date: date,
        dry_run: bool,
        window_days: int,
        google_years: int,
    ) -> int:
        community_set = set(community_names)
        service = GoogleCalendarService(
            calendar_id=GOOGLE_CALENDAR_ID,
            credentials_path=GOOGLE_CALENDAR_CREDENTIALS,
        )

        start = datetime.combine(from_date, datetime.min.time())
        hard_end_date = from_date + timedelta(days=365 * google_years)
        end = datetime.combine(hard_end_date, datetime.max.time())

        matched_ids = set()
        current = start
        while current < end:
            window_end = min(current + timedelta(days=window_days), end)
            events = service.list_events(
                time_min=current,
                time_max=window_end,
                max_results=2500,
            )
            for event in events:
                summary = (event.get("summary") or "").strip()
                if summary not in community_set:
                    continue
                event_date = self._extract_event_date(event)
                if event_date and event_date >= from_date:
                    matched_ids.add(event["id"])
            current = window_end

        self.stdout.write(f"Google削除対象イベント件数: {len(matched_ids)}件")
        if dry_run:
            return len(matched_ids)

        deleted = 0
        for event_id in matched_ids:
            service.delete_event(event_id)
            deleted += 1
        self.stdout.write(self.style.SUCCESS(f"Google削除実行: {deleted}件"))
        return deleted

    def _extract_event_date(self, event) -> date | None:
        start = event.get("start", {})
        if "dateTime" in start:
            try:
                dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                return dt.date()
            except ValueError:
                return None
        if "date" in start:
            try:
                return date.fromisoformat(start["date"])
            except ValueError:
                return None
        return None
