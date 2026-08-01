"""ストレージ設定の回帰テスト.

ローカルの .env.local に R2 認証（AWS_STORAGE_BUCKET_NAME 等）が設定されていても、
テスト実行時は FileSystemStorage にフォールバックする必要がある。offline runner が
外部接続を遮断するため、S3Boto3Storage のままだと head_object 等で通信が発生して失敗する。
参照: app/website/settings/storage.py の _IS_TEST 判定。
"""
from django.conf import settings
from django.test import SimpleTestCase


class StorageSettingsTest(SimpleTestCase):
    """テスト実行時のストレージ設定を検証する。"""

    def test_default_storage_uses_filesystem_during_tests(self) -> None:
        # テスト中は R2 認証の有無に関わらず FileSystemStorage が選ばれること
        self.assertEqual(
            settings.STORAGES['default']['BACKEND'],
            'django.core.files.storage.FileSystemStorage',
        )

    def test_staticfiles_storage_in_debug_mode(self) -> None:
        """DEBUG=True の場合、静的ファイルはローカルストレージを使用する。"""
        if settings.DEBUG:
            self.assertEqual(
                settings.STORAGES['staticfiles']['BACKEND'],
                'django.contrib.staticfiles.storage.StaticFilesStorage',
            )
