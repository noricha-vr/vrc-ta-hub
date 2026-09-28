"""自由記述の【動画撮影】の回答から recording_policy を当て直す（冪等）。

migration 0032 と同じ判定を、recording_policy が public のままで自由記述に
【動画撮影】がある発表だけに当てる。デプロイ中に旧リビジョンが作った申請
（migration 0032 の後に入ったもの）を拾うため、デプロイ完了後に一度流す。
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from event.models import EventDetail
from event.recording_policy_answers import RECORDING_KEYWORD, plan_policy_changes


class Command(BaseCommand):
    help = (
        "recording_policy が public で追加情報に【動画撮影】がある発表に、"
        "回答から決めた撮影の扱いを当て直します。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="変更せず、対象件数と変更内容の要約だけを表示します。",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        candidates = EventDetail.all_objects.filter(
            recording_policy=EventDetail.RecordingPolicy.PUBLIC,
            additional_info__contains=RECORDING_KEYWORD,
        ).order_by("pk").values_list("pk", "additional_info")
        rows = list(candidates)
        changes = plan_policy_changes(rows)

        self.stdout.write(f"対象 EventDetail: {len(rows)}件 (dry_run={dry_run})")
        for policy, pks in sorted(changes.items()):
            self.stdout.write(f"  public -> {policy}: {len(pks)}件 ids={pks}")
        changed = sum(len(pks) for pks in changes.values())
        if not changed:
            self.stdout.write("変更はありません。")
            return
        if dry_run:
            self.stdout.write(f"dry-run のため {changed}件 を変更していません。")
            return

        with transaction.atomic():
            for policy, pks in changes.items():
                EventDetail.all_objects.filter(
                    pk__in=pks, recording_policy=EventDetail.RecordingPolicy.PUBLIC,
                ).update(recording_policy=policy)
        self.stdout.write(self.style.SUCCESS(f"{changed}件 の recording_policy を更新しました。"))
