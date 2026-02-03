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

    def test_save_with_update_fields_including_poster_image_no_new_file(self):
        """update_fieldsにposter_imageが含まれていても、新しいファイルがなければリサイズされないことを確認"""
        # 集会を作成
        community = Community.objects.create(
            name='テスト集会',
            frequency='毎週',
            organizers='テスト主催者'
        )

        # resize_and_convert_imageをモック化
        with patch('community.models.resize_and_convert_image') as mock_resize:
            # poster_imageを含むupdate_fieldsで更新（ただし新しいファイルはなし）
            community.save(update_fields=['poster_image'])

            # _committedがTrueなのでリサイズ処理は呼ばれない
            mock_resize.assert_not_called()

    def test_save_with_new_poster_image_calls_resize(self):
        """新しいファイルがアップロードされた場合、リサイズ処理が実行されることを確認"""
        # 集会を作成
        community = Community.objects.create(
            name='テスト集会',
            frequency='毎週',
            organizers='テスト主催者'
        )

        # 新しい画像をセット
        image_buffer = self._create_test_image()
        new_image = SimpleUploadedFile("new_poster.jpg", image_buffer.read(), content_type="image/jpeg")
        community.poster_image = new_image

        # resize_and_convert_imageをモック化
        with patch('community.models.resize_and_convert_image') as mock_resize:
            community.save(update_fields=['poster_image'])

            # 新しいファイル（_committed=False）があるのでリサイズ処理が呼ばれる
            mock_resize.assert_called_once()

    def test_save_without_update_fields_no_new_file(self):
        """update_fieldsが指定されていなくても、新しいファイルがなければリサイズされないことを確認"""
        # 集会を作成
        community = Community.objects.create(
            name='テスト集会',
            frequency='毎週',
            organizers='テスト主催者'
        )

        # resize_and_convert_imageをモック化
        with patch('community.models.resize_and_convert_image') as mock_resize:
            # update_fieldsなしで保存
            community.name = '更新された集会名'
            community.save()

            # 新しいファイルがないのでリサイズ処理は呼ばれない
            mock_resize.assert_not_called()

    def test_save_without_update_fields_with_new_file(self):
        """update_fieldsが指定されていない場合、新しいファイルがあればリサイズが実行されることを確認"""
        # 集会を作成
        community = Community.objects.create(
            name='テスト集会',
            frequency='毎週',
            organizers='テスト主催者'
        )

        # 新しい画像をセット
        image_buffer = self._create_test_image()
        new_image = SimpleUploadedFile("new_poster.jpg", image_buffer.read(), content_type="image/jpeg")
        community.poster_image = new_image

        # resize_and_convert_imageをモック化
        with patch('community.models.resize_and_convert_image') as mock_resize:
            community.save()

            # 新しいファイルがあるのでリサイズ処理が呼ばれる
            mock_resize.assert_called_once()

    def test_save_with_empty_update_fields(self):
        """update_fieldsが空リストの場合、リサイズ処理がスキップされることを確認"""
        # 集会を作成
        community = Community.objects.create(
            name='テスト集会',
            frequency='毎週',
            organizers='テスト主催者'
        )

        # resize_and_convert_imageをモック化
        with patch('community.models.resize_and_convert_image') as mock_resize:
            # 空のupdate_fieldsで保存
            community.save(update_fields=[])

            # リサイズ処理が呼ばれていないことを確認
            mock_resize.assert_not_called()

    def test_save_with_multiple_update_fields_including_poster_image_no_new_file(self):
        """update_fieldsに複数のフィールドが含まれposter_imageも含まれているが、新しいファイルがない場合"""
        # 集会を作成
        community = Community.objects.create(
            name='テスト集会',
            frequency='毎週',
            organizers='テスト主催者'
        )

        # resize_and_convert_imageをモック化
        with patch('community.models.resize_and_convert_image') as mock_resize:
            # 複数のフィールドを更新（poster_image含む、ただし新しいファイルなし）
            community.name = '更新された集会名'
            community.save(update_fields=['name', 'poster_image', 'description'])

            # 新しいファイルがないのでリサイズ処理は呼ばれない
            mock_resize.assert_not_called()

    def test_save_with_multiple_update_fields_not_including_poster_image(self):
        """update_fieldsに複数のフィールドが含まれるが、poster_imageは含まれていない場合"""
        # 集会を作成
        community = Community.objects.create(
            name='テスト集会',
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

    def test_save_with_broken_poster_image_path_does_not_error(self):
        """壊れたposter_imageパス（poster/poster/poster/...）でもsave()がエラーにならないことを確認"""
        # 集会を作成
        community = Community.objects.create(
            name='テスト集会',
            frequency='毎週',
            organizers='テスト主催者'
        )

        # 壊れたパスをセット（_committedはTrueのまま=既存ファイルとして扱われる）
        # この場合、_committedがTrueなのでリサイズ処理はスキップされ、エラーは発生しない
        with patch.object(community.poster_image, 'name', 'poster/poster/poster/missing.jpg'):
            # エラーが発生せずに保存できることを確認
            community.description = '更新された説明'
            community.save()  # FileNotFoundErrorが発生しないこと

    def test_save_with_committed_true_skips_resize(self):
        """poster_imageの_committedがTrueの場合、リサイズがスキップされることを確認

        既存のposter_image（既にストレージに保存済み）がある場合、
        _committedはTrueになるため、リサイズ処理はスキップされる。
        """
        # 画像付きで集会を作成
        image_buffer = self._create_test_image()
        image = SimpleUploadedFile("test_poster.jpg", image_buffer.read(), content_type="image/jpeg")
        community = Community.objects.create(
            name='テスト集会',
            frequency='毎週',
            organizers='テスト主催者',
            poster_image=image
        )

        # 作成後は_committedがTrueになっている
        self.assertTrue(getattr(community.poster_image, '_committed', True))

        with patch('community.models.resize_and_convert_image') as mock_resize:
            # フィールドを更新せずに保存
            community.name = '更新された名前'
            community.save()

            # _committedがTrueなのでリサイズは呼ばれない
            mock_resize.assert_not_called()

    def test_save_with_committed_false_calls_resize(self):
        """poster_imageの_committedがFalseの場合、リサイズが呼ばれることを確認

        新しいファイルがアップロードされた場合、_committedはFalseになるため、
        リサイズ処理が呼ばれる。
        """
        # 集会を作成
        community = Community.objects.create(
            name='テスト集会',
            frequency='毎週',
            organizers='テスト主催者'
        )

        # 新しい画像をセット（_committed=Falseになる）
        image_buffer = self._create_test_image()
        new_image = SimpleUploadedFile("new_poster.jpg", image_buffer.read(), content_type="image/jpeg")
        community.poster_image = new_image

        # 新しいファイルなので_committedはFalse
        self.assertFalse(getattr(community.poster_image, '_committed', True))

        with patch('community.models.resize_and_convert_image') as mock_resize:
            community.save()

            # _committedがFalseなのでリサイズが呼ばれる
            mock_resize.assert_called_once()
