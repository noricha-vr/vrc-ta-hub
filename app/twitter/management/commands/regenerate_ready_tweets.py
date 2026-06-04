"""ready 状態の TweetQueue を新プロンプトで再生成する管理コマンド

X のスパムフィルタを避ける「本文3行以内」制約は以降の新規生成にのみ効くため、
既に ready キューに入っている旧プロンプト生成分を再生成して X スパムフィルタ
(403) を回避する。
"""
import logging

from django.core.management.base import BaseCommand

from twitter.models import TweetQueue
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

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
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
            body_lines = count_body_lines(item.generated_text)
            validation_errors = validate_tweet_text(item.generated_text)
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
            new_errors = validate_tweet_text(text)
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

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"完了: 更新={updated} 件, 失敗={failed} 件, "
                f"再生成後も制約超過={still_over_limit} 件"
            )
        )
