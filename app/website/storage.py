"""ガイド画像の長期キャッシュを設定する静的ファイルストレージ。"""
from storages.backends.s3boto3 import S3Boto3Storage


class GuideStaticFilesStorage(S3Boto3Storage):
    def get_object_parameters(self, name):
        parameters = super().get_object_parameters(name).copy()
        if name.startswith('guide/images/'):
            parameters['CacheControl'] = 'public, max-age=31536000, immutable'
        return parameters
