import logging
import re
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urlparse

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from event.models import EventDetail

logger = logging.getLogger(__name__)


SELF_DOMAIN_SUFFIX = "vrc-ta-hub.com"

SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", flags=re.IGNORECASE | re.DOTALL)
SCRIPT_SELF_CLOSING_RE = re.compile(r"<script\b[^>]*/\s*>", flags=re.IGNORECASE | re.DOTALL)

# <iframe ...>...</iframe> と <iframe .../> の両方に対応
IFRAME_BLOCK_RE = re.compile(r"<iframe\b[^>]*>.*?</iframe\s*>", flags=re.IGNORECASE | re.DOTALL)
IFRAME_SELF_CLOSING_RE = re.compile(r"<iframe\b[^>]*/\s*>", flags=re.IGNORECASE | re.DOTALL)
IFRAME_SRC_RE = re.compile(r"\bsrc\s*=\s*([\"'])(.*?)\1", flags=re.IGNORECASE | re.DOTALL)

FENCED_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
INLINE_CODE_RE = re.compile(r"`[^`]+`")


@dataclass(frozen=True)
class CleanupResult:
    event_detail_id: int
    contents_changed: bool
    slide_file_detached: bool
    slide_file_deleted: bool
    notes: tuple[str, ...]


def _protect_code(text: str) -> tuple[str, list[str], list[str]]:
    code_blocks: list[str] = []
    inline_codes: list[str] = []

    def protect_code_block(match: re.Match[str]) -> str:
        code_blocks.append(match.group(0))
        return f"\x00CODE_BLOCK_{len(code_blocks) - 1}\x00"

    def protect_inline_code(match: re.Match[str]) -> str:
        inline_codes.append(match.group(0))
        return f"\x00INLINE_CODE_{len(inline_codes) - 1}\x00"

    text = FENCED_CODE_BLOCK_RE.sub(protect_code_block, text)
    text = INLINE_CODE_RE.sub(protect_inline_code, text)
    return text, code_blocks, inline_codes


def _restore_code(text: str, code_blocks: list[str], inline_codes: list[str]) -> str:
    for i, block in enumerate(code_blocks):
        text = text.replace(f"\x00CODE_BLOCK_{i}\x00", block)
    for i, code in enumerate(inline_codes):
        text = text.replace(f"\x00INLINE_CODE_{i}\x00", code)
    return text


def _is_self_domain_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return True
    return parsed.netloc.endswith(SELF_DOMAIN_SUFFIX)


def sanitize_event_detail_contents(contents: str) -> tuple[str, tuple[str, ...]]:
    """EventDetail.contents から危険なHTMLを除去する。

    - <script> は無条件で除去
    - 自ドメイン配下（サブドメイン含む）への <iframe> は除去
    - コードブロック/インラインコードは改変しない

    Args:
        contents: 保存済みのMarkdownテキスト

    Returns:
        (sanitized_contents, notes)
    """
    notes: list[str] = []
    protected, code_blocks, inline_codes = _protect_code(contents)

    before = protected
    protected = SCRIPT_TAG_RE.sub("", protected)
    protected = SCRIPT_SELF_CLOSING_RE.sub("", protected)
    if protected != before:
        notes.append("Removed <script> tag(s)")

    def iframe_replacer(match: re.Match[str]) -> str:
        iframe_html = match.group(0)
        src_match = IFRAME_SRC_RE.search(iframe_html)
        if not src_match:
            # srcがないiframeは意図が不明で、表示側でも削除されるため除去
            notes.append("Removed <iframe> without src")
            return ""
        src = src_match.group(2)
        if _is_self_domain_url(src):
            notes.append("Removed self-domain <iframe>")
            return ""
        return iframe_html

    before = protected
    protected = IFRAME_BLOCK_RE.sub(iframe_replacer, protected)
    protected = IFRAME_SELF_CLOSING_RE.sub(iframe_replacer, protected)
    if protected != before and not any("Removed" in n and "iframe" in n.lower() for n in notes):
        notes.append("Removed <iframe> tag(s)")

    sanitized = _restore_code(protected, code_blocks, inline_codes)
    return sanitized, tuple(notes)


def _is_pdf_magic_bytes(slide_file) -> bool:
    """FileFieldの実体がPDFかをマジックバイトで判定する。"""
    try:
        slide_file.open("rb")
        header = slide_file.read(5)
        slide_file.seek(0)
        return header == b"%PDF-"
    except Exception:
        return False
    finally:
        try:
            slide_file.close()
        except Exception:
            pass


class Command(BaseCommand):
    help = "Stored XSS/偽装アップロード対策: EventDetailのcontents/slide_fileをスキャンして無害化する"

    def add_arguments(self, parser):
        parser.add_argument(
            "--ids",
            type=str,
            default="",
            help="対象のEventDetail IDをカンマ区切りで指定（例: 589,590）",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="DB/ストレージへ反映する（未指定の場合はdry-run）",
        )
        parser.add_argument(
            "--delete-files",
            action="store_true",
            help="不正なslide_fileをストレージから削除する（--apply必須）",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="処理件数を制限（0は無制限）",
        )

    def handle(self, *args, **options):
        apply = bool(options["apply"])
        delete_files = bool(options["delete_files"])
        limit = int(options["limit"])
        ids_raw = (options.get("ids") or "").strip()

        if delete_files and not apply:
            self.stderr.write(self.style.ERROR("--delete-files は --apply とセットで指定してください"))
            return

        target_ids: Optional[list[int]] = None
        if ids_raw:
            try:
                target_ids = [int(x.strip()) for x in ids_raw.split(",") if x.strip()]
            except ValueError:
                self.stderr.write(self.style.ERROR("--ids は数値のカンマ区切りで指定してください"))
                return

        qs = EventDetail.objects.all()
        if target_ids is not None:
            qs = qs.filter(id__in=target_ids)
        else:
            qs = qs.filter(
                Q(contents__icontains="<script")
                | Q(contents__icontains="<iframe")
                | (Q(slide_file__isnull=False) & ~Q(slide_file=""))
            )

        qs = qs.order_by("id")
        if limit > 0:
            qs = qs[:limit]

        self.stdout.write(
            f"sanitize_event_detail_security: targets={qs.count()} apply={apply} delete_files={delete_files}"
        )

        results: list[CleanupResult] = []
        contents_changed_count = 0
        slide_detached_count = 0
        slide_deleted_count = 0

        for event_detail in qs.iterator():
            notes: list[str] = []
            contents_changed = False
            slide_detached = False
            slide_deleted = False

            # contents のクレンジング
            sanitized_contents, content_notes = sanitize_event_detail_contents(event_detail.contents or "")
            if sanitized_contents != (event_detail.contents or ""):
                contents_changed = True
                notes.extend(content_notes)

            # slide_file の検査（偽装アップロードを検知したら切り離す）
            if event_detail.slide_file and getattr(event_detail.slide_file, "name", ""):
                if not _is_pdf_magic_bytes(event_detail.slide_file):
                    slide_detached = True
                    notes.append("Detached invalid slide_file (non-PDF magic bytes)")

            if apply and (contents_changed or slide_detached):
                with transaction.atomic():
                    if contents_changed:
                        event_detail.contents = sanitized_contents

                    if slide_detached:
                        if delete_files:
                            try:
                                original_name = event_detail.slide_file.name
                                event_detail.slide_file.delete(save=False)
                                slide_deleted = True
                                notes.append(f"Deleted slide_file from storage: {original_name}")
                            except Exception as e:
                                notes.append(f"Failed to delete slide_file from storage: {e}")
                        event_detail.slide_file = None

                    update_fields = []
                    if contents_changed:
                        update_fields.append("contents")
                    if slide_detached:
                        update_fields.append("slide_file")
                    event_detail.save(update_fields=update_fields)

            results.append(
                CleanupResult(
                    event_detail_id=event_detail.id,
                    contents_changed=contents_changed,
                    slide_file_detached=slide_detached,
                    slide_file_deleted=slide_deleted,
                    notes=tuple(notes),
                )
            )

            if contents_changed:
                contents_changed_count += 1
            if slide_detached:
                slide_detached_count += 1
            if slide_deleted:
                slide_deleted_count += 1

        for r in results:
            if not (r.contents_changed or r.slide_file_detached or r.slide_file_deleted):
                continue
            self.stdout.write(
                f"- EventDetail id={r.event_detail_id}: contents_changed={r.contents_changed} "
                f"slide_detached={r.slide_file_detached} slide_deleted={r.slide_file_deleted}"
            )
            for note in r.notes:
                self.stdout.write(f"  - {note}")

        self.stdout.write(self.style.SUCCESS("sanitize_event_detail_security: done"))
        self.stdout.write(
            f"Summary: contents_changed={contents_changed_count}, slide_detached={slide_detached_count}, "
            f"slide_deleted={slide_deleted_count}"
        )
