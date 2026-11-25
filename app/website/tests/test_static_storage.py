"""静的ファイルのキャッシュ属性と配信経路を外部接続なしで検証する。"""
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from website.storage import GuideStaticFilesStorage, _static_file_version


class GuideStaticFilesStorageTest(SimpleTestCase):
    def setUp(self):
        self.storage = GuideStaticFilesStorage(
            access_key='test',
            secret_key='test',
            bucket_name='test',
            custom_domain='data.vrc-ta-hub.com',
            object_parameters={},
        )

    def test_guide_images_use_long_term_cache(self):
        for name in ('guide/images/x.webp', 'guide/images/community/create-01-sp.webp'):
            with self.subTest(name=name):
                self.assertEqual(self.storage.get_object_parameters(name), {
                    'CacheControl': 'public, max-age=31536000, immutable',
                })

    def test_other_static_files_keep_existing_parameters(self):
        for name in ('css/x.css', 'guide/x.webp', 'guide/images-other/x.webp', 'guide/images/posters/vrc-ta-hub-poster-1.jpg'):
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

    def test_app_js_and_css_resolve_to_versioned_r2_keys_and_admin_stays_unprefixed(self):
        _static_file_version.cache_clear()
        with TemporaryDirectory() as tmpdir:
            with override_settings(
                STATIC_ROOT=tmpdir,
                STORAGES={
                    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
                },
            ):
                call_command('collectstatic', '--noinput', verbosity=0)

            with override_settings(STATIC_ROOT=tmpdir):
                for asset in (
                    'event/js/recording-cuts-timeline.js',
                    'event/js/recording-cuts.js',
                    'event/js/recording-review-player.js',
                    'event/js/recording-review-submit.js',
                    'event/js/recording-review-timeline.js',
                    'event/js/recording-review.js',
                    'event/css/recording-review.css',
                    'ta_hub/css/admin-ui.css',
                    'ta_hub/js/admin-ui.js',
                ):
                    with self.subTest(asset=asset):
                        resolved = self.storage.url(asset)
                        self.assertRegex(
                            resolved,
                            rf'^https://data\.vrc-ta-hub\.com/_assets/[0-9a-f]{{10}}/{asset}$',
                        )
                        self.assertTrue((Path(tmpdir) / asset).is_file())

                # サードパーティ資産（相対 url() を含む admin CSS など）や画像は従来 R2 パスを維持する
                self.assertEqual(
                    self.storage.url('admin/css/base.css'),
                    'https://data.vrc-ta-hub.com/admin/css/base.css',
                )
                self.assertEqual(
                    self.storage.url('admin/img/icon-addlink.svg'),
                    'https://data.vrc-ta-hub.com/admin/img/icon-addlink.svg',
                )
                self.assertEqual(
                    self.storage.url('guide/images/community/create-01.webp'),
                    'https://data.vrc-ta-hub.com/guide/images/community/create-01.webp',
                )

    def test_traversal_and_symlink_escape_do_not_resolve_to_versioned_key(self):
        from website.storage import versioned_asset_key

        _static_file_version.cache_clear()
        with TemporaryDirectory() as tmpdir, TemporaryDirectory() as outside_dir:
            root = Path(tmpdir)
            outside = Path(outside_dir) / 'secret.js'
            outside.write_text('secret', encoding='utf-8')
            (root / 'event' / 'js').mkdir(parents=True)
            (root / 'event' / 'js' / 'leak.js').symlink_to(outside)

            self.assertIsNone(versioned_asset_key('event/js/../../etc/passwd.js', static_root=root))
            self.assertIsNone(versioned_asset_key('event/js/leak.js', static_root=root))
