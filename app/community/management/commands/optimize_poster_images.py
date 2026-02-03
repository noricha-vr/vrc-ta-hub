"""
Community ポスター画像の一括最適化コマンド

使用方法:
    # ドライラン（変更を適用しない）
    python manage.py optimize_poster_images --dry-run

    # 実行
    python manage.py optimize_poster_images
"""
from django.core.management.base import BaseCommand
from PIL import Image

from community.models import Community
from ta_hub.libs import (
    DEFAULT_JPEG_QUALITY,
    DEFAULT_MAX_SIZE,
    DEFAULT_PNG_TO_JPEG_THRESHOLD,
    resize_and_convert_image,
)


class Command(BaseCommand):
    help = 'Community のポスター画像を一括最適化'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='変更を適用せずに対象ファイルを表示',
        )
        parser.add_argument(
            '--max-size',
            type=int,
            default=DEFAULT_MAX_SIZE,
            help=f'リサイズ後の最大サイズ (デフォルト: {DEFAULT_MAX_SIZE}px)',
        )
        parser.add_argument(
            '--jpeg-quality',
            type=int,
            default=DEFAULT_JPEG_QUALITY,
            help=f'JPEG圧縮品質 (デフォルト: {DEFAULT_JPEG_QUALITY})',
        )
        parser.add_argument(
            '--png-threshold',
            type=int,
            default=DEFAULT_PNG_TO_JPEG_THRESHOLD,
            help=f'PNG→JPEG変換の閾値バイト数 (デフォルト: {DEFAULT_PNG_TO_JPEG_THRESHOLD})',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        max_size = options['max_size']
        jpeg_quality = options['jpeg_quality']
        png_threshold = options['png_threshold']

        if dry_run:
            self.stdout.write(self.style.WARNING('=== ドライランモード（変更は適用されません） ==='))

        self.stdout.write(f'設定: max_size={max_size}px, jpeg_quality={jpeg_quality}, png_threshold={png_threshold // 1024}KB')

        # 統計情報
        total_count = 0
        resized_count = 0
        converted_count = 0
        skipped_count = 0
        error_count = 0

        communities = Community.objects.exclude(poster_image='').exclude(poster_image__isnull=True)
        total_communities = communities.count()

        self.stdout.write(f'\n対象: {total_communities} 件の Community\n')

        for i, community in enumerate(communities, 1):
            total_count += 1
            poster = community.poster_image

            try:
                # ファイル情報を取得
                file_path = poster.name
                file_size = poster.size if poster else 0

                # 画像を開いてサイズと形式を確認
                # 注意: with 文を使うと PIL がファイルを閉じてしまい、
                # 後続の poster.file.seek(0) が失敗するため try-finally を使用
                img = None
                try:
                    img = Image.open(poster.file)
                    width, height = img.size
                    original_format = img.format or 'UNKNOWN'

                    # 透過チェック
                    has_transparency = img.mode in ('RGBA', 'LA') or (
                        img.mode == 'P' and 'transparency' in img.info
                    )
                finally:
                    # PIL 画像オブジェクトのみ解放（poster.file は閉じない）
                    if img is not None:
                        del img

                # 処理が必要かどうか判定
                needs_resize = width > max_size or height > max_size
                needs_convert = (
                    original_format == 'PNG' and
                    file_size >= png_threshold and
                    not has_transparency
                )

                if not needs_resize and not needs_convert:
                    skipped_count += 1
                    continue

                # 処理内容を表示
                actions = []
                if needs_resize:
                    actions.append(f'リサイズ: {width}x{height} → {max_size}px以下')
                if needs_convert:
                    actions.append(f'変換: PNG → JPEG ({file_size // 1024}KB, 透過なし)')

                self.stdout.write(f'[{i}/{total_communities}] {community.name}')
                self.stdout.write(f'  パス: {file_path}')
                for action in actions:
                    self.stdout.write(f'  {action}')

                if needs_resize:
                    resized_count += 1
                if needs_convert:
                    converted_count += 1

                if not dry_run:
                    # 実際に最適化を実行
                    # ファイルを再度開く（PIL が閉じている可能性があるため）
                    poster.file.seek(0)

                    # _committed を False に設定して resize_and_convert_image が処理するようにする
                    poster._committed = False

                    resize_and_convert_image(
                        poster,
                        max_size=max_size,
                        jpeg_quality=jpeg_quality,
                        png_to_jpeg_threshold=png_threshold
                    )

                    # リサイズ完了後、_committed を True に設定して
                    # Community.save() での二重リサイズを防ぐ
                    poster._committed = True

                    # モデルを保存（poster_image フィールドのみ更新）
                    community.save(update_fields=['poster_image'])
                    self.stdout.write(self.style.SUCCESS(f'  → 完了: {poster.name}'))

            except FileNotFoundError:
                error_count += 1
                self.stdout.write(self.style.ERROR(f'[{i}/{total_communities}] {community.name}: ファイルが見つかりません'))
            except Exception as e:
                error_count += 1
                self.stdout.write(self.style.ERROR(f'[{i}/{total_communities}] {community.name}: エラー - {e}'))
            finally:
                # ファイルハンドルを確実に閉じる（リソースリーク防止）
                try:
                    poster.file.close()
                except Exception:
                    pass  # 既に閉じている場合は無視

        # サマリー
        self.stdout.write('\n' + '=' * 50)
        self.stdout.write('サマリー')
        self.stdout.write('=' * 50)
        self.stdout.write(f'  対象ファイル数: {total_count}')
        self.stdout.write(f'  リサイズ対象: {resized_count}')
        self.stdout.write(f'  PNG→JPEG変換対象: {converted_count}')
        self.stdout.write(f'  スキップ（処理不要）: {skipped_count}')
        self.stdout.write(f'  エラー: {error_count}')

        if dry_run:
            self.stdout.write(self.style.WARNING('\nドライランモードのため、変更は適用されていません。'))
            self.stdout.write('実際に適用するには --dry-run オプションを外して再実行してください。')
