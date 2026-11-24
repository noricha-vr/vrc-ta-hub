"""ready 状態の TweetQueue を新プロンプトで再生成する管理コマンド

X のスパムフィルタを避ける「本文3行以内」制約は以降の新規生成にのみ効くため、
既に ready キューに入っている旧プロンプト生成分を再生成して X スパムフィルタ
(403) を回避する。
"""
import logging

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from event.models import Event
from twitter.models import MisskeyNote, TweetQueue
from twitter.scheduling import default_scheduled_at
from twitter.services.announcement_eligibility import (
    LEGACY_SAME_DAY_INDIVIDUAL_SKIP_REASON,
    PRESERVED_MISSKEY_STATUSES,
    event_date_skip_reason,
    presentation_detail_skip_reason,
)
from twitter.tweet_generator import (
    count_body_lines,
    count_tweet_length,
    get_generator,
    get_tweet_image_url,
    validate_tweet_text,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "ready 状態の TweetQueue を新プロンプトで再生成する"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="対象と現在の行数を表示するだけで DB を更新しない",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="本文3行以内のキューも含めて全 ready キューを対象にする",
        )
        parser.add_argument(
            "--pk",
            type=int,
            default=None,
            help="特定キューPKのみを対象にする（検証用）",
        )
        parser.add_argument(
            "--tweet-type",
            dest="tweet_type",
            default=None,
            choices=[
                "new_community", "lt", "special",
                "daily_reminder", "slide_share",
            ],
            help="tweet_type で絞り込み",
        )
        parser.add_argument(
            "--revive-legacy-same-day",
            action="store_true",
            help="旧仕様の当日リマインド統合でスキップされた未開始の発表告知キューを一度きり復帰させる",
        )

    def _revive_legacy_same_day_skipped(self, *, dry_run: bool) -> None:
        current = timezone.now()
        local_today = timezone.localdate(current)
        future_event_ids = list(
            Event.objects.filter(date__gte=local_today).values_list("pk", flat=True),
        )
        if future_event_ids:
            candidates = list(
                TweetQueue.objects.filter(
                    event_id__in=future_event_ids,
                    tweet_type__in=("lt", "special"),
                    status="skipped",
                    error_message=LEGACY_SAME_DAY_INDIVIDUAL_SKIP_REASON,
                ).select_related("community", "event", "event_detail").order_by("-pk"),
            )
        else:
            candidates = []
        targets = []
        seen_detail_keys: set[tuple[int, str]] = set()
        for item in candidates:
            if event_date_skip_reason(item, now=current) or presentation_detail_skip_reason(item):
                continue
            if item.event_detail_id is not None:
                key = (item.event_detail_id, item.tweet_type)
                if key in seen_detail_keys:
                    continue
                if (
                    TweetQueue.objects.filter(
                        event_detail_id=item.event_detail_id,
                        tweet_type=item.tweet_type,
                    )
                    .exclude(pk=item.pk)
                    .filter(
                        ~Q(status="skipped")
                        | Q(misskey_note__status__in=PRESERVED_MISSKEY_STATUSES)
                    )
                    .exists()
                ):
                    continue
                seen_detail_keys.add(key)
            targets.append(item)

        self.stdout.write(
            f"LEGACY_REVIVE_CANDIDATES: count={len(targets)} dry_run={dry_run}"
        )
        if dry_run:
            for item in targets:
                self.stdout.write(f"  #{item.pk} [{item.tweet_type}] event={item.event_id} -> (dry-run)")
            return

        revived = 0
        for item in targets:
            item.status = "generation_failed"
            item.scheduled_at = default_scheduled_at(
                item.tweet_type, event=item.event, base_datetime=current,
            )
            item.generated_text = ""
            item.error_message = "当日リマインド廃止に伴い個別告知へ復帰"
            item.generation_token = ""
            item.save(
                update_fields=[
                    "status",
                    "scheduled_at",
                    "generated_text",
                    "error_message",
                    "generation_token",
                ],
            )
            MisskeyNote.objects.filter(
                tweet_queue=item,
                status="skipped",
            ).update(
                status="pending",
                error_message="",
                attempts=0,
                updated_at=current,
            )
            revived += 1

        self.stdout.write(
            self.style.SUCCESS(f"LEGACY_REVIVE_APPLIED: revived={revived}")
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if options["revive_legacy_same_day"]:
            self._revive_legacy_same_day_skipped(dry_run=dry_run)
            return

        regenerate_all = options["all"]
        pk = options["pk"]
        tweet_type = options["tweet_type"]

        queryset = TweetQueue.objects.filter(status="ready").select_related(
            "community", "event", "event_detail"
        )
        if pk is not None:
            queryset = queryset.filter(pk=pk)
        if tweet_type:
            queryset = queryset.filter(tweet_type=tweet_type)

        candidates = list(queryset)
        if not candidates:
            self.stdout.write("対象の ready キューが見つかりませんでした。")
            return

        targets = []
        for item in candidates:
            forbid_urls = item.tweet_type in ("lt", "special")
            body_lines = count_body_lines(item.generated_text)
            validation_errors = validate_tweet_text(
                item.generated_text,
                forbid_urls=forbid_urls,
            )
            image_url = get_tweet_image_url(item) if item.tweet_type == "slide_share" else ""
            needs_image_sync = bool(image_url and item.image_url != image_url)
            needs_text_regen = regenerate_all or bool(validation_errors)
            if needs_text_regen or needs_image_sync:
                targets.append((
                    item, body_lines, validation_errors, image_url,
                    needs_text_regen,
                ))

        self.stdout.write(
            f"ready キュー: {len(candidates)} 件, 再生成対象: {len(targets)} 件 "
            f"(全件モード={regenerate_all})"
        )

        updated = 0
        failed = 0
        still_over_limit = 0

        for item, old_lines, old_errors, image_url, needs_text_regen in targets:
            old_weighted = count_tweet_length(item.generated_text)
            prefix = (
                f"#{item.pk} [{item.tweet_type}] "
                f"旧weighted={old_weighted} 旧行数={old_lines}"
            )
            if old_errors:
                prefix = f"{prefix} ({', '.join(old_errors)})"
            if dry_run:
                self.stdout.write(f"  {prefix} -> (dry-run skip)")
                continue

            if not needs_text_regen:
                item.image_url = image_url
                item.save(update_fields=["image_url"])
                updated += 1
                self.stdout.write(f"  {prefix} -> 画像URLを再同期 (OK)")
                continue

            generator = get_generator(item.tweet_type)
            if not generator:
                self.stdout.write(
                    self.style.WARNING(f"  {prefix} -> 生成関数が見つからない (skip)")
                )
                failed += 1
                continue

            try:
                text = generator(item)
            except Exception:
                logger.exception("Regeneration raised exception for queue %d", item.pk)
                self.stdout.write(
                    self.style.ERROR(f"  {prefix} -> 例外発生 (skip)")
                )
                failed += 1
                continue

            if not text:
                self.stdout.write(
                    self.style.WARNING(f"  {prefix} -> 生成失敗 None (skip)")
                )
                failed += 1
                continue

            new_lines = count_body_lines(text)
            new_weighted = count_tweet_length(text)
            new_errors = validate_tweet_text(
                text,
                forbid_urls=(item.tweet_type in ("lt", "special")),
            )
            item.generated_text = text

            if image_url or not item.image_url:
                image_url = image_url or get_tweet_image_url(item)
                if image_url:
                    item.image_url = image_url
                    item.save(update_fields=["generated_text", "image_url"])
                else:
                    item.save(update_fields=["generated_text"])
            else:
                item.save(update_fields=["generated_text"])

            updated += 1
            if new_errors:
                still_over_limit += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"  {prefix} -> 新weighted={new_weighted} 新行数={new_lines} "
                        f"({', '.join(new_errors)})"
                    )
                )
            else:
                self.stdout.write(f"  {prefix} -> 新weighted={new_weighted} 新行数={new_lines} (OK)")

        summary = (
            f"更新={updated} 件, 失敗={failed} 件, "
            f"再生成後も制約超過={still_over_limit} 件"
        )
        self.stdout.write("")

        # 1件でも再生成に失敗したら異常終了させる。
        # cron / 手動実行のどちらでも「一部だけ失敗」を見落とさないため。
        if failed:
            raise CommandError(f"再生成に失敗したキューがある: {summary}")

        self.stdout.write(self.style.SUCCESS(f"完了: {summary}"))
