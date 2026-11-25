"""sync_container_static 管理コマンドの検証。"""
from __future__ import annotations

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

from website.storage import _static_file_version, versioned_asset_key


def _not_found_error() -> ClientError:
    return ClientError({'Error': {'Code': '404', 'Message': 'Not Found'}}, 'HeadObject')


class SyncContainerStaticCommandTests(SimpleTestCase):
    def setUp(self) -> None:
        _static_file_version.cache_clear()

    def test_skips_when_debug_is_true_without_force(self) -> None:
        out = StringIO()
        with override_settings(DEBUG=True):
            call_command('sync_container_static', stdout=out)
        self.assertIn('DEBUG=True', out.getvalue())

    @patch('ta_hub.management.commands.sync_container_static.boto3.client')
    def test_uploads_missing_app_assets_and_skips_existing(self, mock_boto: MagicMock) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / 'event' / 'js').mkdir(parents=True)
            (root / 'event' / 'css').mkdir(parents=True)
            (root / 'admin' / 'css').mkdir(parents=True)
            (root / 'guide' / 'images').mkdir(parents=True)

            js_file = root / 'event' / 'js' / 'recording-review.js'
            css_file = root / 'event' / 'css' / 'recording-review.css'
            js_file.write_text('console.log(1);', encoding='utf-8')
            css_file.write_text('.a{color:blue;}', encoding='utf-8')
            # サードパーティ / 画像は対象外
            (root / 'admin' / 'css' / 'base.css').write_text('body{}', encoding='utf-8')
            (root / 'guide' / 'images' / 'x.webp').write_bytes(b'webp')

            js_key = versioned_asset_key('event/js/recording-review.js', static_root=root)
            css_key = versioned_asset_key('event/css/recording-review.css', static_root=root)
            self.assertIsNotNone(js_key)
            self.assertIsNotNone(css_key)

            mock_client = MagicMock()

            def head_side_effect(*, Bucket: str, Key: str):
                if Key == js_key:
                    raise _not_found_error()
                return {'ContentLength': 10}

            mock_client.head_object.side_effect = head_side_effect
            mock_boto.return_value = mock_client

            out = StringIO()
            with override_settings(
                DEBUG=False,
                STATIC_ROOT=tmpdir,
                AWS_STORAGE_BUCKET_NAME='vrc-ta-hub',
                AWS_S3_ENDPOINT_URL='https://r2.example.invalid',
                AWS_ACCESS_KEY_ID='key',
                AWS_SECRET_ACCESS_KEY='secret',
            ):
                call_command('sync_container_static', stdout=out)

            self.assertIn('uploaded=1 skipped=1', out.getvalue())
            mock_client.put_object.assert_called_once()
            put_kwargs = mock_client.put_object.call_args.kwargs
            self.assertEqual(put_kwargs['Bucket'], 'vrc-ta-hub')
            self.assertEqual(put_kwargs['Key'], js_key)
            self.assertEqual(put_kwargs['ContentType'], 'text/javascript; charset=utf-8')
            self.assertEqual(put_kwargs['CacheControl'], 'public, max-age=31536000, immutable')

    @patch('ta_hub.management.commands.sync_container_static.time.sleep')
    @patch('ta_hub.management.commands.sync_container_static.boto3.client')
    def test_retries_and_raises_command_error_when_r2_fails(self, mock_boto: MagicMock, mock_sleep: MagicMock) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / 'event' / 'js').mkdir(parents=True)
            (root / 'event' / 'js' / 'recording-review.js').write_text('console.log(1);', encoding='utf-8')

            mock_client = MagicMock()
            mock_client.head_object.side_effect = RuntimeError('r2 timeout')
            mock_boto.return_value = mock_client

            with override_settings(
                DEBUG=False,
                STATIC_ROOT=tmpdir,
                AWS_STORAGE_BUCKET_NAME='vrc-ta-hub',
                AWS_S3_ENDPOINT_URL='https://r2.example.invalid',
                AWS_ACCESS_KEY_ID='key',
                AWS_SECRET_ACCESS_KEY='secret',
            ):
                with self.assertRaises(CommandError):
                    call_command('sync_container_static')

            self.assertEqual(mock_client.head_object.call_count, 3)
            self.assertEqual(mock_sleep.call_count, 2)

    def test_raises_command_error_when_r2_config_missing_in_production(self) -> None:
        with override_settings(
            DEBUG=False,
            AWS_STORAGE_BUCKET_NAME='',
            AWS_S3_ENDPOINT_URL='https://r2.example.invalid',
            AWS_ACCESS_KEY_ID='key',
            AWS_SECRET_ACCESS_KEY='secret',
        ):
            with self.assertRaises(CommandError):
                call_command('sync_container_static')

    def test_collect_targets_rejects_symlinks_pointing_outside_static_root(self) -> None:
        from ta_hub.management.commands.sync_container_static import _collect_targets

        with TemporaryDirectory() as tmpdir, TemporaryDirectory() as outside_dir:
            root = Path(tmpdir)
            outside = Path(outside_dir) / 'secret.js'
            outside.write_text('secret', encoding='utf-8')
            (root / 'event' / 'js').mkdir(parents=True)
            valid = root / 'event' / 'js' / 'ok.js'
            valid.write_text('console.log(1);', encoding='utf-8')
            symlink_target = root / 'event' / 'js' / 'leak.js'
            symlink_target.symlink_to(outside)

            targets = _collect_targets(root)
            self.assertEqual([p.name for p, _ in targets], ['ok.js'])
