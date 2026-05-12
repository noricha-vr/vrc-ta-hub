import logging

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from event.libs import ensure_pdf_thumbnail
from event.models import EventDetail
from twitter.signals import sync_slide_share_queue_image

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """PDFスライドからEventDetailサムネイルを一括再生成する."""

    help = "slide_file がある EventDetail のサムネイルを PDF 先頭ページから再生成します。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="対象件数とIDだけを表示し、サムネイルは生成しません。",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="既存サムネイルがある EventDetail も PDF 先頭ページで上書きします。",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="処理件数の上限を指定します。動作確認用です。",
        )
        parser.add_argument(
            "--ids",
            help="対象 EventDetail ID をカンマ区切りで指定します。",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]
        limit = options.get("limit")
        target_ids = self._parse_ids(options.get("ids"))

        queryset = EventDetail.objects.exclude(slide_file="").exclude(slide_file__isnull=True).order_by("pk")
        if not force:
            queryset = queryset.filter(Q(thumbnail_image="") | Q(thumbnail_image__isnull=True))
        if target_ids is not None:
            queryset = queryset.filter(pk__in=target_ids)
        if limit is not None:
            if limit < 1:
                raise CommandError("--limit は1以上を指定してください。")
            queryset = queryset[:limit]

        event_details = list(queryset)
        self.stdout.write(
            f"対象 EventDetail: {len(event_details)}件 "
            f"(force={force}, dry_run={dry_run})"
        )

        if dry_run:
            for event_detail in event_details:
                self.stdout.write(
                    f"DRY-RUN id={event_detail.pk} slide_file={event_detail.slide_file.name} "
                    f"thumbnail_image={event_detail.thumbnail_image.name or '-'}"
                )
            return

        generated = 0
        skipped = 0
        failed = []

        for event_detail in event_details:
            try:
                if ensure_pdf_thumbnail(event_detail, save=True, overwrite=force):
                    generated += 1
                    sync_slide_share_queue_image(event_detail)
                    self.stdout.write(
                        f"UPDATED id={event_detail.pk} thumbnail_image={event_detail.thumbnail_image.name}"
                    )
                else:
                    skipped += 1
                    self.stdout.write(f"SKIPPED id={event_detail.pk}")
            except Exception as exc:
                logger.exception("EventDetail PDFサムネイル再生成に失敗しました: id=%s", event_detail.pk)
                failed.append((event_detail.pk, str(exc)))
                self.stderr.write(f"FAILED id={event_detail.pk}: {exc}")

        self.stdout.write(
            self.style.SUCCESS(
                f"完了: updated={generated}, skipped={skipped}, failed={len(failed)}"
            )
        )
        if failed:
            raise CommandError(f"{len(failed)}件のサムネイル生成に失敗しました。")

    def _parse_ids(self, ids_value: str | None) -> list[int] | None:
        if not ids_value:
            return None
        try:
            return [int(value.strip()) for value in ids_value.split(",") if value.strip()]
        except ValueError as exc:
            raise CommandError("--ids は数値IDのカンマ区切りで指定してください。") from exc
