"""
poster_imageフィールドのパス重複を修正するマネジメントコマンド。

poster/poster/poster/.../filename.jpeg のようなパスを
poster/filename.jpeg に修正する。
"""
import re

from django.core.management.base import BaseCommand

from community.models import Community


class Command(BaseCommand):
    """Communityのposter_imageパス重複を修正するコマンド。"""

    help = 'poster_imageフィールドの重複パス（poster/poster/...）を修正する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='実際には変更せず、修正対象を表示するのみ',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('ドライランモード: 実際には変更しません\n'))

        # 全Communityを取得（poster_imageがあるもののみ）
        communities = Community.objects.exclude(poster_image='')

        # poster/の重複を検出するパターン
        # poster/poster/... のようなパスにマッチ
        duplicate_pattern = re.compile(r'^(poster/)+')

        fixed_count = 0
        error_count = 0

        for community in communities:
            original_path = community.poster_image.name

            if not original_path:
                continue

            # パスの重複をチェック
            match = duplicate_pattern.match(original_path)
            if match and match.group(0) != 'poster/':
                # 重複がある場合、ファイル名を抽出
                # poster/poster/poster/filename.jpeg -> filename.jpeg
                filename = duplicate_pattern.sub('', original_path)
                new_path = f'poster/{filename}'

                self.stdout.write(f'[Community ID: {community.id}] {community.name}')
                self.stdout.write(f'  修正前: {original_path}')
                self.stdout.write(f'  修正後: {new_path}')

                if not dry_run:
                    try:
                        community.poster_image.name = new_path
                        # update_fieldsを指定してリサイズ処理をスキップ
                        # _committed=Trueなのでsave()内のリサイズ処理もスキップされる
                        community.save(update_fields=['poster_image'])
                        self.stdout.write(self.style.SUCCESS('  -> 修正完了'))
                        fixed_count += 1
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f'  -> エラー: {e}'))
                        error_count += 1
                else:
                    fixed_count += 1

                self.stdout.write('')

        # サマリー表示
        self.stdout.write('\n' + '=' * 50)
        if dry_run:
            self.stdout.write(self.style.WARNING(f'修正対象: {fixed_count}件'))
            self.stdout.write('実際に修正するには --dry-run オプションを外して実行してください')
        else:
            self.stdout.write(self.style.SUCCESS(f'修正完了: {fixed_count}件'))
            if error_count > 0:
                self.stdout.write(self.style.ERROR(f'エラー: {error_count}件'))
