"""fix_poster_pathsマネジメントコマンドのテスト。"""
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase

from community.models import Community


class FixPosterPathsCommandTest(TestCase):
    """fix_poster_pathsコマンドのテスト。"""

    def setUp(self):
        """テスト用のCommunityを作成。"""
        # poster_imageを持つCommunityを作成（saveをモックしてリサイズをスキップ）
        self.community_normal = Community.objects.create(
            name='正常パスの集会',
            frequency='毎週',
            organizers='主催者A',
        )
        # 正常なパス
        self.community_normal.poster_image.name = 'poster/normal.jpeg'
        Community.objects.filter(pk=self.community_normal.pk).update(
            poster_image='poster/normal.jpeg'
        )
        self.community_normal.refresh_from_db()

        self.community_duplicate = Community.objects.create(
            name='重複パスの集会',
            frequency='毎週',
            organizers='主催者B',
        )
        # 重複パス
        Community.objects.filter(pk=self.community_duplicate.pk).update(
            poster_image='poster/poster/duplicate.jpeg'
        )
        self.community_duplicate.refresh_from_db()

        self.community_triple = Community.objects.create(
            name='三重パスの集会',
            frequency='毎週',
            organizers='主催者C',
        )
        # 三重パス
        Community.objects.filter(pk=self.community_triple.pk).update(
            poster_image='poster/poster/poster/triple.jpeg'
        )
        self.community_triple.refresh_from_db()

        self.community_empty = Community.objects.create(
            name='画像なしの集会',
            frequency='毎週',
            organizers='主催者D',
        )
        # 画像なし（空文字列）

    def test_dry_run_does_not_modify(self):
        """--dry-runオプションで実際には変更されないことを確認。"""
        out = StringIO()
        call_command('fix_poster_paths', '--dry-run', stdout=out)

        # 重複パスの集会はそのままのはず
        self.community_duplicate.refresh_from_db()
        self.assertEqual(
            self.community_duplicate.poster_image.name,
            'poster/poster/duplicate.jpeg'
        )

        # 出力にドライランモードの表示があること
        output = out.getvalue()
        self.assertIn('ドライランモード', output)
        self.assertIn('修正対象: 2件', output)

    def test_fix_duplicate_paths(self):
        """重複パスが正しく修正されることを確認。"""
        out = StringIO()
        call_command('fix_poster_paths', stdout=out)

        # 正常パスは変更なし
        self.community_normal.refresh_from_db()
        self.assertEqual(
            self.community_normal.poster_image.name,
            'poster/normal.jpeg'
        )

        # 重複パスが修正される
        self.community_duplicate.refresh_from_db()
        self.assertEqual(
            self.community_duplicate.poster_image.name,
            'poster/duplicate.jpeg'
        )

        # 三重パスも修正される
        self.community_triple.refresh_from_db()
        self.assertEqual(
            self.community_triple.poster_image.name,
            'poster/triple.jpeg'
        )

        # 出力に修正完了の表示があること
        output = out.getvalue()
        self.assertIn('修正完了: 2件', output)

    def test_empty_poster_image_skipped(self):
        """poster_imageが空のCommunityはスキップされることを確認。"""
        out = StringIO()
        call_command('fix_poster_paths', stdout=out)

        output = out.getvalue()
        # 画像なしの集会は処理対象外
        self.assertNotIn('画像なしの集会', output)

    def test_output_shows_before_and_after(self):
        """修正前と修正後のパスが表示されることを確認。"""
        out = StringIO()
        call_command('fix_poster_paths', stdout=out)

        output = out.getvalue()
        self.assertIn('修正前:', output)
        self.assertIn('修正後:', output)
        self.assertIn('poster/poster/duplicate.jpeg', output)
        self.assertIn('poster/duplicate.jpeg', output)
