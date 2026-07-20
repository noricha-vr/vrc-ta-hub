"""Grok/X Searchによる集会活動監視を実行する管理コマンド。"""

import json

from django.core.management.base import BaseCommand, CommandError

from community.activity_client import ActivityMonitorError
from community.activity_monitor import run_community_activity_checks


class Command(BaseCommand):
    help = "GrokのX Searchで集会の活動状況を確認し、通知・自動非表示を行います。"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="DB更新・通知・非表示を行わない")
        parser.add_argument("--force", action="store_true", help="確認間隔を無視して再確認する")
        parser.add_argument("--limit", type=int, help="最大確認件数（1〜100）")
        parser.add_argument("--community-id", type=int, help="指定した集会IDだけを確認する")
        auto_hide_group = parser.add_mutually_exclusive_group()
        auto_hide_group.add_argument(
            "--auto-hide",
            action="store_true",
            dest="auto_hide",
            help="環境変数に関係なく自動非表示を有効化する",
        )
        auto_hide_group.add_argument(
            "--no-auto-hide",
            action="store_false",
            dest="auto_hide",
            help="環境変数に関係なく通知だけにする",
        )
        parser.set_defaults(auto_hide=None)

    def handle(self, *args, **options):
        limit = options["limit"]
        community_id = options["community_id"]
        if limit is not None and not 1 <= limit <= 100:
            raise CommandError("--limit は1〜100で指定してください。")
        if community_id is not None and community_id < 1:
            raise CommandError("--community-id は1以上で指定してください。")

        try:
            summary = run_community_activity_checks(
                dry_run=options["dry_run"],
                force=options["force"],
                limit=limit,
                community_id=community_id,
                auto_hide=options["auto_hide"],
            )
        except ActivityMonitorError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        if summary["errors"]:
            self.stderr.write(self.style.WARNING(f'{summary["errors"]}件の確認でエラーが発生しました。'))
        else:
            self.stdout.write(self.style.SUCCESS("集会活動確認が完了しました。"))
