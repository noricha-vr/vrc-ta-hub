"""既存の vrc.group 短縮URLを正規URLに一括変換する管理コマンド。"""

from django.core.management.base import BaseCommand

from community.libs import resolve_vrc_group_url
from community.models import Community


class Command(BaseCommand):
    """group_url に vrc.group を含む Community を正規URLに変換する。"""

    help = "vrc.group 短縮URLを vrchat.com 正規URLに一括変換する"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="実際には変更せず、変換結果を表示するのみ",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(
                self.style.WARNING("ドライランモード: 実際には変更しません\n")
            )

        communities = Community.objects.filter(group_url__contains="vrc.group")
        if not communities.exists():
            self.stdout.write("vrc.group を含む group_url はありません。")
            return

        resolved_count = 0
        error_count = 0
        skipped_count = 0

        for community in communities:
            original_url = community.group_url
            resolved_url = resolve_vrc_group_url(original_url)

            self.stdout.write(f"[ID: {community.id}] {community.name}")
            self.stdout.write(f"  before: {original_url}")
            self.stdout.write(f"  after:  {resolved_url}")

            if resolved_url == original_url:
                self.stdout.write(self.style.WARNING("  -> 解決できませんでした"))
                skipped_count += 1
                continue

            if not dry_run:
                try:
                    community.group_url = resolved_url
                    community.save(update_fields=["group_url"])
                    self.stdout.write(self.style.SUCCESS("  -> 更新完了"))
                    resolved_count += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  -> エラー: {e}"))
                    error_count += 1
            else:
                resolved_count += 1

            self.stdout.write("")

        # サマリー表示
        self.stdout.write("\n" + "=" * 50)
        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"変換対象: {resolved_count}件")
            )
            self.stdout.write(
                "実際に変換するには --dry-run オプションを外して実行してください"
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"変換完了: {resolved_count}件")
            )
            if error_count > 0:
                self.stdout.write(
                    self.style.ERROR(f"エラー: {error_count}件")
                )
        if skipped_count > 0:
            self.stdout.write(
                self.style.WARNING(f"解決不可: {skipped_count}件")
            )
