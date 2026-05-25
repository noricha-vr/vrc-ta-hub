"""既存のEventDetail.slide_fileをUUID形式パスに移行する。

PR #340 で導入した slide_file_upload_to は新規アップロードのみに作用するため、
既存ファイル（slide/<元ファイル名>.pdf）を手動でUUID化する必要がある。

R2バケットはローカル・本番で共有しているため、以下の4フェーズに分けて安全に進める:

  1. mapping   : DBをスキャンし、旧名→新UUID名のマッピングをJSONに出力
  2. copy      : R2上で旧→新へファイルをコピー（旧ファイルは残す）
  3. update-db : DBの slide_file.name と contents 内URLを新名に書き換え
  4. cleanup   : 動作確認後、R2の旧ファイルを削除（任意）

各フェーズは --apply 指定時のみ実体を変更する（デフォルト dry-run）。

phase=update-db は同一マッピングJSONで本番DBに対しても再実行できるよう冪等に設計。
"""

import json
import re
import uuid
from pathlib import Path
from urllib.parse import quote

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from event.models import EventDetail


def _build_url_variants(media_host: str, old_name: str, new_name: str) -> list[tuple[str, str]]:
    """contents 内に現れうる旧URL表記を新URLにマップする組を返す。

    Markdown 記事内では日本語ファイル名がパーセントエンコードされて保存されているケースと、
    生 Unicode のまま保存されているケースが混在する。両方を網羅する。
    """
    base = f"https://{media_host}"
    raw_old = f"{base}/{old_name}"
    encoded_old = f"{base}/{quote(old_name, safe='/')}"
    new_url = f"{base}/{new_name}"
    variants = [(raw_old, new_url)]
    if encoded_old != raw_old:
        variants.append((encoded_old, new_url))
    return variants


_UUID_PDF_RE = re.compile(r'^slide/[0-9a-f]{32}\.pdf$')


class Command(BaseCommand):
    help = "既存スライドPDFをUUID形式パスに移行する（phase: mapping/copy/update-db/cleanup）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--phase",
            required=True,
            choices=["mapping", "copy", "update-db", "cleanup", "verify"],
        )
        parser.add_argument("--mapping-file", required=True)
        parser.add_argument("--apply", action="store_true", help="実体を変更する（指定なしはdry-run）")
        parser.add_argument(
            "--media-host",
            default="data.vrc-ta-hub.com",
            help="contents 内URL書き換えで対象とするホスト名",
        )

    def handle(self, *args, **opts):
        phase = opts["phase"]
        mapping_path = Path(opts["mapping_file"])
        apply_changes = opts["apply"]
        media_host = opts["media_host"]

        if phase == "mapping":
            self._phase_mapping(mapping_path, apply_changes)
        elif phase == "copy":
            self._phase_copy(mapping_path, apply_changes)
        elif phase == "update-db":
            self._phase_update_db(mapping_path, apply_changes, media_host)
        elif phase == "verify":
            self._phase_verify(mapping_path, media_host)
        elif phase == "cleanup":
            self._phase_cleanup(mapping_path, apply_changes, media_host)

    def _phase_mapping(self, path: Path, apply_changes: bool) -> None:
        qs = EventDetail.objects.exclude(Q(slide_file="") | Q(slide_file__isnull=True)).order_by("pk")
        mapping: dict[str, str] = {}
        skipped = 0
        for ed in qs:
            old = ed.slide_file.name
            if _UUID_PDF_RE.match(old):
                skipped += 1
                continue
            if old in mapping:
                continue
            mapping[old] = f"slide/{uuid.uuid4().hex}.pdf"

        self.stdout.write(
            f"total_with_slide_file={qs.count()} to_rename={len(mapping)} already_uuid={skipped}"
        )
        for o, n in list(mapping.items())[:3]:
            self.stdout.write(f"  {o} -> {n}")

        if apply_changes:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"saved mapping: {path}"))
        else:
            self.stdout.write(self.style.WARNING("DRY-RUN: mapping not saved"))

    def _phase_copy(self, path: Path, apply_changes: bool) -> None:
        mapping = self._load_mapping(path)
        copied = 0
        skipped_existing = 0
        missing_source = 0
        errors = 0

        for old, new in mapping.items():
            if not default_storage.exists(old):
                self.stderr.write(f"MISSING source: {old}")
                missing_source += 1
                continue
            if default_storage.exists(new):
                skipped_existing += 1
                continue
            if not apply_changes:
                copied += 1
                continue
            try:
                with default_storage.open(old, "rb") as src:
                    default_storage.save(new, src)
                copied += 1
                if copied % 20 == 0:
                    self.stdout.write(f"  copied={copied}/{len(mapping)}")
            except Exception as exc:
                self.stderr.write(f"ERROR copying {old} -> {new}: {exc}")
                errors += 1

        verb = "COPIED" if apply_changes else "DRY-RUN COPY"
        msg = (
            f"{verb}: copied={copied} skipped_existing={skipped_existing} "
            f"missing_source={missing_source} errors={errors}"
        )
        if errors or missing_source:
            raise CommandError(msg)
        self.stdout.write(self.style.SUCCESS(msg))

    def _phase_update_db(self, path: Path, apply_changes: bool, media_host: str) -> None:
        mapping = self._load_mapping(path)
        url_pairs: list[tuple[str, str]] = []
        for old, new in mapping.items():
            url_pairs.extend(_build_url_variants(media_host, old, new))

        # DB を更新する前に、移行先 R2 オブジェクトが全件存在することを検証する。
        # copy 未実行・一部失敗・別環境の古いマッピングを使った場合に
        # 公開 PDF を一括 404 化するのを防ぐ。
        missing_targets = [new for new in mapping.values() if not default_storage.exists(new)]
        if missing_targets:
            sample = ", ".join(missing_targets[:5])
            raise CommandError(
                f"update-db aborted: {len(missing_targets)}/{len(mapping)} target objects missing in R2. "
                f"Run `copy` first. example: {sample}"
            )

        file_updated = 0
        contents_updated = 0

        with transaction.atomic():
            # slide_file.name を一括UPDATE
            for old, new in mapping.items():
                count = EventDetail.objects.filter(slide_file=old).update(slide_file=new)
                file_updated += count

            # contents URL の書き換え（対象EDを集めて1件ずつ処理）
            target_pks: set[int] = set()
            for old_url, _ in url_pairs:
                target_pks.update(
                    EventDetail.objects.filter(contents__contains=old_url).values_list("pk", flat=True)
                )

            for pk in sorted(target_pks):
                ed = EventDetail.objects.get(pk=pk)
                new_contents = ed.contents
                for old_url, new_url in url_pairs:
                    if old_url in new_contents:
                        new_contents = new_contents.replace(old_url, new_url)
                if new_contents != ed.contents:
                    ed.contents = new_contents
                    ed.save(update_fields=["contents"])
                    contents_updated += 1

            if not apply_changes:
                transaction.set_rollback(True)

        verb = "APPLIED" if apply_changes else "DRY-RUN"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb}: slide_file_updated={file_updated} contents_updated={contents_updated}"
            )
        )

    def _phase_verify(self, path: Path, media_host: str) -> None:
        """update-db 後の検証: 旧名・旧URLの残存をスキャン"""
        mapping = self._load_mapping(path)
        old_names = set(mapping)
        old_urls: set[str] = set()
        for old in old_names:
            for u, _ in _build_url_variants(media_host, old, "_"):
                old_urls.add(u)

        residual_files = (
            EventDetail.objects.filter(slide_file__in=list(old_names)).count()
        )
        residual_contents_pks: set[int] = set()
        for old_url in old_urls:
            residual_contents_pks.update(
                EventDetail.objects.filter(contents__contains=old_url).values_list("pk", flat=True)
            )

        # R2上の新ファイル存在確認（サンプリング）
        new_names = list(mapping.values())
        sample = new_names[:5] + (new_names[-5:] if len(new_names) > 5 else [])
        missing_in_r2 = [n for n in sample if not default_storage.exists(n)]

        self.stdout.write(
            f"residual_slide_file={residual_files} residual_contents={len(residual_contents_pks)} "
            f"r2_sample_missing={len(missing_in_r2)}/{len(sample)}"
        )
        if missing_in_r2:
            for n in missing_in_r2:
                self.stderr.write(f"  R2 missing: {n}")

        # 検証フェーズとして機能させるため、不整合があれば異常終了させる。
        # exit code を失敗にすることで自動実行・運用手順から cleanup へ
        # 誤って進まないようにする。
        if residual_files or residual_contents_pks or missing_in_r2:
            raise CommandError(
                f"verify failed: residual_slide_file={residual_files}, "
                f"residual_contents={len(residual_contents_pks)}, "
                f"r2_sample_missing={len(missing_in_r2)}/{len(sample)}"
            )

    def _phase_cleanup(self, path: Path, apply_changes: bool, media_host: str = "data.vrc-ta-hub.com") -> None:
        """R2上の旧ファイルを削除する。update-db / verify 完了後に実行する想定。"""
        mapping = self._load_mapping(path)
        deleted = 0
        not_found = 0
        still_referenced = 0

        for old in mapping:
            # DB上で旧名がまだ参照されていないことを確認（防御的）
            if EventDetail.objects.filter(slide_file=old).exists():
                still_referenced += 1
                self.stderr.write(f"STILL REFERENCED in slide_file: {old}")
                continue
            # contents 内に旧URLが残っているなら記事リンク切れを誘発するので削除しない
            old_urls = [u for u, _ in _build_url_variants(media_host, old, "_")]
            if any(EventDetail.objects.filter(contents__contains=u).exists() for u in old_urls):
                still_referenced += 1
                self.stderr.write(f"STILL REFERENCED in contents: {old}")
                continue
            if not default_storage.exists(old):
                not_found += 1
                continue
            if apply_changes:
                default_storage.delete(old)
            deleted += 1

        verb = "DELETED" if apply_changes else "DRY-RUN DELETE"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb}: deleted={deleted} not_found={not_found} still_referenced={still_referenced}"
            )
        )

    @staticmethod
    def _load_mapping(path: Path) -> dict[str, str]:
        if not path.exists():
            raise CommandError(f"mapping file not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
