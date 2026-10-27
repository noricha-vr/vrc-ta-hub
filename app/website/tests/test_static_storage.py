"""静的ファイルのキャッシュ属性を外部接続なしで検証する。"""
from django.test import SimpleTestCase

from website.storage import GuideStaticFilesStorage


class GuideStaticFilesStorageTest(SimpleTestCase):
    def setUp(self):
        self.storage = GuideStaticFilesStorage(
            access_key='test', secret_key='test', bucket_name='test', object_parameters={},
        )

    def test_guide_images_use_long_term_cache(self):
        for name in ('guide/images/x.webp', 'guide/images/community/create-01-sp.webp'):
            with self.subTest(name=name):
                self.assertEqual(self.storage.get_object_parameters(name), {
                    'CacheControl': 'public, max-age=31536000, immutable',
                })

    def test_other_static_files_keep_existing_parameters(self):
        for name in ('css/x.css', 'guide/x.webp', 'guide/images-other/x.webp'):
            with self.subTest(name=name):
                self.assertEqual(self.storage.get_object_parameters(name), {})

    def test_existing_parameters_are_preserved_without_mutation(self):
        parameters = {'ContentDisposition': 'inline', 'CacheControl': 'max-age=60'}
        self.storage.object_parameters = parameters
        image_parameters = self.storage.get_object_parameters('guide/images/x.webp')
        self.assertEqual(image_parameters['ContentDisposition'], 'inline')
        self.assertEqual(image_parameters['CacheControl'], 'public, max-age=31536000, immutable')
        self.assertEqual(self.storage.get_object_parameters('css/x.css'), parameters)
        self.assertEqual(parameters['CacheControl'], 'max-age=60')
