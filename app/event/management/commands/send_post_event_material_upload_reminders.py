"""発表翌日の資料アップロード依頼メールを送信するコマンド。"""
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from event.material_upload_reminders import send_material_upload_reminders


class Command(BaseCommand):
    help = "発表翌日に、資料アップロード依頼メールを送信します。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            dest="target_date",
            help="対象の開催日。未指定時は昨日です。形式: YYYY-MM-DD",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="メール送信とログ記録を行わず、対象と判定結果だけを表示します。",
        )

    def handle(self, *args, **options):
        target_date = self._parse_target_date(options.get("target_date"))
        dry_run = bool(options.get("dry_run"))
        results = send_material_upload_reminders(target_date=target_date, dry_run=dry_run)

        self.stdout.write(f"target_date={target_date} dry_run={dry_run} total={len(results)}")
        for result in results:
            self.stdout.write(
                "event_detail_id={event_detail_id} email={email} action={action} "
                "confidence={confidence} matched_intent={matched_intent} reason={reason}".format(
                    event_detail_id=result.event_detail_id,
                    email=result.email,
                    action=result.action,
                    confidence=result.confidence or "-",
                    matched_intent=result.matched_intent or "-",
                    reason=result.reason,
                )
            )

    def _parse_target_date(self, value: str | None):
        if not value:
            return timezone.localdate() - timedelta(days=1)
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise CommandError("--date は YYYY-MM-DD 形式で指定してください。") from exc
