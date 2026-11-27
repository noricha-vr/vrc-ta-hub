"""E2E 検証用に記事自動生成キューを実行する管理コマンド（views/article_generation.py と同一サービス関数を呼ぶ）。"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from event.services.article_generation import (
    DEFAULT_BATCH_SIZE,
    MAX_BATCH_SIZE,
    process_article_generation_queue,
)


class Command(BaseCommand):
    help = "E2E 検証用に process_article_generation_queue を実行し JSON を返す。"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=DEFAULT_BATCH_SIZE)

    def handle(self, *args, **options):
        from .e2e_check_isolation import validate_e2e_isolation

        iso = validate_e2e_isolation()
        if not iso["ok"]:
            self.stderr.write(json.dumps(iso, ensure_ascii=False))
            raise SystemExit(2)

        limit = max(1, min(MAX_BATCH_SIZE, int(options["limit"])))
        result = process_article_generation_queue(limit=limit)
        result["hub_sha"] = iso["hub_sha"]
        self.stdout.write(json.dumps(result, ensure_ascii=False, default=str))
