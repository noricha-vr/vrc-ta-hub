"""コンテナ起動時にアプリ同梱 JS/CSS をハッシュ付きキーで R2 へ冪等同期するコマンド。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import mimetypes
from pathlib import Path
import time
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from website.storage import is_versioned_app_asset, versioned_asset_key

logger = logging.getLogger(__name__)

_CONTENT_TYPE_BY_SUFFIX: dict[str, str] = {
    '.js': 'text/javascript; charset=utf-8',
    '.mjs': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.map': 'application/json; charset=utf-8',
}
_CACHE_CONTROL = 'public, max-age=31536000, immutable'
_NOT_FOUND_CODES = frozenset({'404', 'NoSuchKey', 'NotFound'})


def _guess_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _CONTENT_TYPE_BY_SUFFIX:
        return _CONTENT_TYPE_BY_SUFFIX[suffix]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or 'application/octet-stream'


def _collect_targets(static_root: Path) -> list[tuple[Path, str]]:
    if not static_root.is_dir():
        return []
    targets: list[tuple[Path, str]] = []
    for path in sorted(static_root.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(static_root).as_posix()
        if not is_versioned_app_asset(rel):
            continue
        key = versioned_asset_key(rel, static_root=static_root)
        if key:
            targets.append((path, key))
    return targets


def _sync_single_asset(
    client: Any,
    *,
    bucket: str,
    path: Path,
    key: str,
    dry_run: bool,
    max_attempts: int = 3,
    backoff_base_sec: float = 0.5,
) -> str:
    content_type = _guess_content_type(path)
    body: bytes | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            try:
                client.head_object(Bucket=bucket, Key=key)
                return 'skipped'
            except ClientError as exc:
                err_code = str(exc.response.get('Error', {}).get('Code', ''))
                if err_code not in _NOT_FOUND_CODES:
                    raise
            if dry_run:
                return 'would_upload'
            if body is None:
                body = path.read_bytes()
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                CacheControl=_CACHE_CONTROL,
            )
            return 'uploaded'
        except Exception:
            if attempt >= max_attempts:
                raise
            time.sleep(backoff_base_sec * (2 ** (attempt - 1)))
    return 'skipped'


class Command(BaseCommand):
    help = 'アプリ同梱 JS/CSS（_assets/<hash>/...）を共有 R2 バケットへ冪等同期する'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='DEBUG=True でも同期を実行する')
        parser.add_argument('--dry-run', action='store_true', help='アップロードを行わず対象件数のみ確認する')

    def handle(self, *args, **options):
        force = bool(options.get('force'))
        dry_run = bool(options.get('dry_run'))

        if settings.DEBUG and not force:
            self.stdout.write('DEBUG=True のため sync_container_static をスキップします。')
            return

        bucket = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', None)
        endpoint_url = getattr(settings, 'AWS_S3_ENDPOINT_URL', None)
        access_key = getattr(settings, 'AWS_ACCESS_KEY_ID', None)
        secret_key = getattr(settings, 'AWS_SECRET_ACCESS_KEY', None)
        if not (bucket and endpoint_url and access_key and secret_key):
            self.stdout.write('R2 設定が未構成のため sync_container_static をスキップします。')
            return

        static_root_raw = getattr(settings, 'STATIC_ROOT', None)
        if not static_root_raw:
            raise CommandError('STATIC_ROOT が設定されていません。')
        static_root = Path(static_root_raw)
        targets = _collect_targets(static_root)
        if not targets:
            self.stdout.write('同期対象のアプリ JS/CSS が見つかりません。')
            return

        client = boto3.client(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(connect_timeout=3, read_timeout=5, retries={'max_attempts': 1}),
        )

        counts = {'uploaded': 0, 'skipped': 0, 'would_upload': 0}
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(
                    _sync_single_asset,
                    client,
                    bucket=bucket,
                    path=path,
                    key=key,
                    dry_run=dry_run,
                ): key
                for path, key in targets
            }
            for fut in as_completed(futures):
                key = futures[fut]
                try:
                    status = fut.result()
                    counts[status] += 1
                except Exception as exc:
                    errors.append(f'{key}: {exc}')

        if errors:
            raise CommandError(f'アプリ JS/CSS の R2 同期に失敗しました ({len(errors)} 件): {"; ".join(errors[:5])}')

        self.stdout.write(
            f'sync_container_static 完了: uploaded={counts["uploaded"]} '
            f'skipped={counts["skipped"]} would_upload={counts["would_upload"]}'
        )
