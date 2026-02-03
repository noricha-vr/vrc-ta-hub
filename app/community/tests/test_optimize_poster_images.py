"""optimize_poster_images コマンドのテスト"""
from io import BytesIO, StringIO
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from PIL import Image

from community.models import Community

CustomUser = get_user_model()


class OptimizePosterImagesTestCase(TestCase):
    """optimize_poster_images コマンドのテスト"""

    def _create_test_image(self, width=100, height=100, format='JPEG', mode='RGB'):
        """テスト用の画像を作成"""
        img = Image.new(mode, (width, height), color='red')
        buffer = BytesIO()
        img.save(buffer, format=format)
        buffer.seek(0)
        return buffer

    def test_dry_run_does_not_modify(self):
        """ドライランモードでは実際のファイルは変更されないことを確認"""
        out = StringIO()

        # コミュニティを作成（ポスター画像なし）
        Community.objects.create(
            name='テスト集会',
            frequency='毎週',
            organizers='テスト主催者'
        )

        # ドライランで実行
        call_command('optimize_poster_images', '--dry-run', stdout=out)

        output = out.getvalue()
        self.assertIn('ドライランモード', output)

    def test_command_handles_missing_poster_image(self):
        """ポスター画像がないコミュニティはスキップされることを確認"""
        out = StringIO()

        # ポスター画像なしのコミュニティを作成
        Community.objects.create(
            name='ポスターなし集会',
            frequency='毎週',
            organizers='テスト主催者'
        )

        # 実行
        call_command('optimize_poster_images', '--dry-run', stdout=out)

        output = out.getvalue()
        # 対象が0件
        self.assertIn('対象: 0 件', output)

    def test_command_summary_output(self):
        """コマンドがサマリーを出力することを確認"""
        out = StringIO()

        # 実行
        call_command('optimize_poster_images', '--dry-run', stdout=out)

        output = out.getvalue()
        self.assertIn('サマリー', output)
        self.assertIn('対象ファイル数', output)
        self.assertIn('リサイズ対象', output)
        self.assertIn('PNG→JPEG変換対象', output)
        self.assertIn('スキップ', output)
        self.assertIn('エラー', output)

    def test_custom_parameters(self):
        """カスタムパラメータが受け付けられることを確認"""
        out = StringIO()

        # カスタムパラメータで実行
        call_command(
            'optimize_poster_images',
            '--dry-run',
            '--max-size', '500',
            '--jpeg-quality', '75',
            '--png-threshold', '102400',
            stdout=out
        )

        output = out.getvalue()
        self.assertIn('max_size=500px', output)
        self.assertIn('jpeg_quality=75', output)
        self.assertIn('png_threshold=100KB', output)

    def test_file_handle_not_closed_after_image_info_extraction(self):
        """画像情報取得後もファイルハンドルが閉じられていないことを確認

        PIL の with 文を使用するとファイルが閉じられてしまい、
        その後の seek(0) が失敗する問題の回帰テスト
        """
        # 大きな画像（リサイズ対象）を作成
        # デフォルト max_size=1000px なので 2000px の画像はリサイズ対象
        large_image = self._create_test_image(width=2000, height=2000, format='JPEG')
        uploaded_file = SimpleUploadedFile(
            name='large_test.jpg',
            content=large_image.read(),
            content_type='image/jpeg'
        )

        # Community.save() の resize_and_convert_image をスキップして
        # 大きい画像のままDBに保存
        with patch('community.models.resize_and_convert_image'):
            community = Community.objects.create(
                name='ファイルハンドルテスト集会',
                frequency='毎週',
                organizers='テスト主催者',
                poster_image=uploaded_file
            )

        out = StringIO()

        # 非ドライランモードで実行（実際に処理を行う）
        # ファイルハンドルが閉じられていると ValueError: I/O operation on closed file が発生
        try:
            call_command('optimize_poster_images', stdout=out)
        except ValueError as e:
            if 'I/O operation on closed file' in str(e):
                self.fail('ファイルハンドルが閉じられています: ' + str(e))
            raise

        output = out.getvalue()
        # リサイズが実行されたことを確認
        self.assertIn('リサイズ', output)
        self.assertIn('完了', output)

        # クリーンアップ
        community.poster_image.delete()
        community.delete()
