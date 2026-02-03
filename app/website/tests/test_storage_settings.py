"""ストレージ設定のテスト

DEBUG環境でも本番環境でもメディアファイルはR2を使用し、
静的ファイルのみDEBUGで分岐することを確認する。
"""

from django.test import TestCase, override_settings
from django.conf import settings


class StorageSettingsTest(TestCase):
    """ストレージ設定のテスト"""

    def test_media_storage_uses_s3_backend(self):
        """メディアストレージはS3バックエンド(R2)を使用する"""
        self.assertEqual(
            settings.DEFAULT_FILE_STORAGE,
            'storages.backends.s3boto3.S3Boto3Storage'
        )

    def test_media_url_points_to_r2(self):
        """MEDIA_URLはR2のエンドポイントを指す"""
        # R2のエンドポイントURLが含まれていることを確認
        self.assertIn('r2.cloudflarestorage.com', settings.MEDIA_URL)

    def test_s3_file_overwrite_is_disabled(self):
        """ファイル上書きは無効になっている"""
        self.assertFalse(settings.AWS_S3_FILE_OVERWRITE)

    def test_querystring_auth_is_disabled(self):
        """認証付きURLは生成しない設定になっている"""
        self.assertFalse(settings.AWS_QUERYSTRING_AUTH)

    def test_staticfiles_storage_in_debug_mode(self):
        """DEBUG=Trueの場合、静的ファイルはローカルストレージを使用"""
        if settings.DEBUG:
            self.assertEqual(
                settings.STATICFILES_STORAGE,
                'django.contrib.staticfiles.storage.StaticFilesStorage'
            )
