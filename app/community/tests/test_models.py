"""Communityモデルのテスト"""
from io import BytesIO
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from community.models import Community

CustomUser = get_user_model()


class CommunitySaveMethodTestCase(TestCase):
    """Community.save()メソッドのテスト"""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='testpass123',
            user_name='テストユーザー'
        )

    def _create_test_image(self, width=100, height=100, format='JPEG'):
        """テスト用の画像を作成"""
        img = Image.new('RGB', (width, height), color='red')
        buffer = BytesIO()
        img.save(buffer, format=format)
        buffer.seek(0)
        return buffer

    def test_save_with_update_fields_not_including_poster_image(self):
        """update_fieldsにposter_imageが含まれていない場合、リサイズ処理がスキップされることを確認"""
        # 集会を作成
        community = Community.objects.create(
            name='テスト集会',
            custom_user=self.user,
            frequency='毎週',
            organizers='テスト主催者'
        )

        # resize_and_convert_imageをモック化
        with patch('community.models.resize_and_convert_image') as mock_resize:
            # notification_webhook_urlのみを更新
            community.notification_webhook_url = 'https://discord.com/api/webhooks/123/abc'
            community.save(update_fields=['notification_webhook_url'])

            # リサイズ処理が呼ばれていないことを確認
            mock_resize.assert_not_called()

    def test_save_with_update_fields_including_poster_image(self):
        """update_fieldsにposter_imageが含まれている場合、リサイズ処理が実行されることを確認"""
        # 集会を作成
        community = Community.objects.create(
            name='テスト集会',
            custom_user=self.user,
            frequency='毎週',
            organizers='テスト主催者'
        )

        # resize_and_convert_imageをモック化
        with patch('community.models.resize_and_convert_image') as mock_resize:
            # poster_imageを含むupdate_fieldsで更新
            community.save(update_fields=['poster_image'])

            # リサイズ処理が呼ばれることを確認
            mock_resize.assert_called_once()

    def test_save_without_update_fields(self):
        """update_fieldsが指定されていない場合、リサイズ処理が実行されることを確認"""
        # 集会を作成
        community = Community.objects.create(
            name='テスト集会',
            custom_user=self.user,
            frequency='毎週',
            organizers='テスト主催者'
        )

        # resize_and_convert_imageをモック化
        with patch('community.models.resize_and_convert_image') as mock_resize:
            # update_fieldsなしで保存
            community.name = '更新された集会名'
            community.save()

            # リサイズ処理が呼ばれることを確認
            mock_resize.assert_called_once()

    def test_save_with_empty_update_fields(self):
        """update_fieldsが空リストの場合、リサイズ処理がスキップされることを確認"""
        # 集会を作成
        community = Community.objects.create(
            name='テスト集会',
            custom_user=self.user,
            frequency='毎週',
            organizers='テスト主催者'
        )

        # resize_and_convert_imageをモック化
        with patch('community.models.resize_and_convert_image') as mock_resize:
            # 空のupdate_fieldsで保存
            community.save(update_fields=[])

            # リサイズ処理が呼ばれていないことを確認
            mock_resize.assert_not_called()

    def test_save_with_multiple_update_fields_including_poster_image(self):
        """update_fieldsに複数のフィールドが含まれ、poster_imageも含まれている場合"""
        # 集会を作成
        community = Community.objects.create(
            name='テスト集会',
            custom_user=self.user,
            frequency='毎週',
            organizers='テスト主催者'
        )

        # resize_and_convert_imageをモック化
        with patch('community.models.resize_and_convert_image') as mock_resize:
            # 複数のフィールドを更新（poster_image含む）
            community.name = '更新された集会名'
            community.save(update_fields=['name', 'poster_image', 'description'])

            # リサイズ処理が呼ばれることを確認
            mock_resize.assert_called_once()

    def test_save_with_multiple_update_fields_not_including_poster_image(self):
        """update_fieldsに複数のフィールドが含まれるが、poster_imageは含まれていない場合"""
        # 集会を作成
        community = Community.objects.create(
            name='テスト集会',
            custom_user=self.user,
            frequency='毎週',
            organizers='テスト主催者'
        )

        # resize_and_convert_imageをモック化
        with patch('community.models.resize_and_convert_image') as mock_resize:
            # 複数のフィールドを更新（poster_image含まない）
            community.name = '更新された集会名'
            community.description = '新しい説明'
            community.save(update_fields=['name', 'description', 'notification_webhook_url'])

            # リサイズ処理が呼ばれていないことを確認
            mock_resize.assert_not_called()
