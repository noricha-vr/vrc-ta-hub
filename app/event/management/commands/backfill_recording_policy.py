"""自由記述の【動画撮影】の回答から recording_policy を当て直す（冪等）。

migration 0032 と同じ判定を、recording_policy が public のままで自由記述に
【動画撮影】がある発表だけに当てる。デプロイ中に旧リビジョンが作った申請
（migration 0032 の後に入ったもの）を拾うため、デプロイ完了後に一度流す。
--since には 0032 を流した時刻を渡す。それより前の発表は 0032 が判定済みで、
新しい画面で明示的に「公開」を選んだ発表を古い自由記述で上書きしないため。

期間内の public の発表で、自由記述に拒否の回答が残っていれば forbidden にする。
新しい画面で公開を選んでいても、答えが食い違っている時は撮らない側に倒す（意図した仕様）。
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from event.models import EventDetail
from event.recording_policy_answers import RECORDING_KEYWORD, plan_policy_changes


def _parse_iso_datetime(value):
    """ISO 8601 の日時を aware な datetime にする。タイムゾーン無しは設定のタイムゾーンとみなす。"""
    parsed = parse_datetime(value)
    if parsed is None:
        raise CommandError(f"日時の形式が正しくありません（ISO 8601）: {value}")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


class Command(BaseCommand):
    help = (
        "recording_policy が public で追加情報に【動画撮影】がある発表に、"
        "回答から決めた撮影の扱いを当て直します。拒否の回答が残っていれば、"
        "画面で公開を選んでいても撮らない側（forbidden）に倒します（意図した仕様）。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="変更せず、対象件数と変更内容の要約だけを表示します。",
        )
        parser.add_argument(
            "--since",
            required=True,
            type=_parse_iso_datetime,
            help="この日時（ISO 8601、例: 2026-10-01T12:00:00+09:00）以降に作られた発表だけを対象にします。",
        )
        parser.add_argument(
            "--until",
            type=_parse_iso_datetime,
            help="この日時より前に作られた発表だけを対象にします（任意）。",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        since, until = options["since"], options["until"]
        if until is not None and until <= since:
            raise CommandError("--until は --since より後を指定してください。")
        candidates = EventDetail.all_objects.filter(
            recording_policy=EventDetail.RecordingPolicy.PUBLIC,
            additional_info__contains=RECORDING_KEYWORD,
            created_at__gte=since,
        )
        if until is not None:
            candidates = candidates.filter(created_at__lt=until)
        rows = list(candidates.order_by("pk").values_list("pk", "additional_info"))
        changes = plan_policy_changes(rows)

        self.stdout.write(
            f"対象 EventDetail: {len(rows)}件 "
            f"(since={since.isoformat()}, until={until.isoformat() if until else '-'}, dry_run={dry_run})"
        )
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
