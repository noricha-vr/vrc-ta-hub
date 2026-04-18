"""ready 状態の TweetQueue を新プロンプトで再生成する管理コマンド

PR #252 で導入した「本文3行以内」制約は以降の新規生成にのみ効くため、
既に ready キューに入っている旧プロンプト生成分を再生成して X スパムフィルタ
(403) を回避する。
"""
import logging

from django.core.management.base import BaseCommand

from twitter.models import TweetQueue
from twitter.tweet_generator import (
    MAX_BODY_LINES,
    count_body_lines,
    get_generator,
    get_poster_image_url,
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
            needs_regen = regenerate_all or body_lines > MAX_BODY_LINES
            if needs_regen:
                targets.append((item, body_lines))

        self.stdout.write(
            f"ready キュー: {len(candidates)} 件, 再生成対象: {len(targets)} 件 "
            f"(全件モード={regenerate_all})"
        )

        updated = 0
        failed = 0
        still_over_limit = 0

        for item, old_lines in targets:
            prefix = f"#{item.pk} [{item.tweet_type}] 旧行数={old_lines}"
            if dry_run:
                self.stdout.write(f"  {prefix} -> (dry-run skip)")
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
            item.generated_text = text

            if not item.image_url:
                image_url = get_poster_image_url(item.community)
                if image_url:
                    item.image_url = image_url
                    item.save(update_fields=["generated_text", "image_url"])
                else:
                    item.save(update_fields=["generated_text"])
            else:
                item.save(update_fields=["generated_text"])

            updated += 1
            if new_lines > MAX_BODY_LINES:
                still_over_limit += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"  {prefix} -> 新行数={new_lines} (依然として制約超過)"
                    )
                )
            else:
                self.stdout.write(f"  {prefix} -> 新行数={new_lines} (OK)")

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"完了: 更新={updated} 件, 失敗={failed} 件, "
                f"再生成後も制約超過={still_over_limit} 件"
            )
        )
