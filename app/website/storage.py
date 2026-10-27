"""ガイド画像の長期キャッシュを設定する静的ファイルストレージ。"""
from storages.backends.s3boto3 import S3Boto3Storage


class GuideStaticFilesStorage(S3Boto3Storage):
    def get_object_parameters(self, name):
        parameters = super().get_object_parameters(name).copy()
        # 本文のスクリーンショット（.webp）は URL に中身のハッシュが付くので長期でよい。
        # 配布用ポスター（posters/*.jpg）は固定 URL で直接リンクされるため対象外。
        if name.startswith('guide/images/') and name.endswith('.webp'):
            parameters['CacheControl'] = 'public, max-age=31536000, immutable'
        return parameters
