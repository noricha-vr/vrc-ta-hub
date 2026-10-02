"""Djangoから分離してPDFテキスト抽出とサムネイル描画を実行するworker."""

from __future__ import annotations

import argparse
import errno
import resource
import sys
from pathlib import Path

WORKER_ADDRESS_SPACE_BYTES = 512 * 1024 * 1024
WORKER_DATA_BYTES = 256 * 1024 * 1024
WORKER_FILE_BYTES = 8 * 1024 * 1024
WORKER_CPU_SOFT_SECONDS = 20
WORKER_CPU_HARD_SECONDS = 21
RESOURCE_LIMIT_EXIT_CODE = 3


def _add_application_root_to_path() -> None:
    """-I実行時も、信頼済みアプリ内の純粋な画像helperだけを参照可能にする."""
    app_root = str(Path(__file__).resolve().parent)
    if app_root not in sys.path:
        sys.path.insert(0, app_root)


def _apply_resource_limits() -> None:
    """PDFライブラリを読み込む前にworker自身のOS資源上限を設定する."""
    resource.setrlimit(
        resource.RLIMIT_AS,
        (WORKER_ADDRESS_SPACE_BYTES, WORKER_ADDRESS_SPACE_BYTES),
    )
    resource.setrlimit(
        resource.RLIMIT_DATA,
        (WORKER_DATA_BYTES, WORKER_DATA_BYTES),
    )
    resource.setrlimit(
        resource.RLIMIT_FSIZE,
        (WORKER_FILE_BYTES, WORKER_FILE_BYTES),
    )
    resource.setrlimit(
        resource.RLIMIT_CPU,
        (WORKER_CPU_SOFT_SECONDS, WORKER_CPU_HARD_SECONDS),
    )


def extract_pdf_text(
    pdf_path: str,
    *,
    max_chars: int,
    max_pages: int | None = None,
    reader_factory=None,
) -> str:
    """既存のページ数・文字数budgetでPDF本文を抽出する."""
    _add_application_root_to_path()
    from event.pdf_processing import MAX_PDF_TEXT_PAGES

    if max_pages is None:
        max_pages = MAX_PDF_TEXT_PAGES
    if reader_factory is None:
        from pypdf import PdfReader

        reader_factory = PdfReader

    reader = reader_factory(pdf_path)
    page_count = len(reader.pages)
    page_texts = []
    current_chars = 0
    for page_index in range(min(page_count, max_pages)):
        page = reader.pages[page_index]
        text = page.extract_text() or ""
        if not text:
            continue

        remaining_chars = max_chars - current_chars
        if remaining_chars <= 0:
            break

        page_texts.append(text[:remaining_chars])
        current_chars += min(len(text), remaining_chars) + 1

    return "\n".join(page_texts)


def render_pdf_thumbnail(pdf_path: str, output_path: str, *, pdf_document_factory=None) -> None:
    """PDFの先頭ページを既存の描画上限でJPEGへ保存する."""
    _add_application_root_to_path()
    import pypdfium2 as pdfium
    from event.pdf_processing import get_pdf_thumbnail_render_scale
    from event.thumbnail import crop_to_slide_thumbnail_aspect_ratio

    if pdf_document_factory is None:
        pdf_document_factory = pdfium.PdfDocument

    pdf = pdf_document_factory(pdf_path)
    try:
        page = pdf[0]
        try:
            bitmap = page.render(scale=get_pdf_thumbnail_render_scale(page))
            try:
                image = crop_to_slide_thumbnail_aspect_ratio(bitmap.to_pil().convert("RGB"))
            finally:
                if hasattr(bitmap, "close"):
                    bitmap.close()
        finally:
            if hasattr(page, "close"):
                page.close()

        with open(output_path, "xb") as output_file:
            image.save(output_file, format="JPEG", quality=85, optimize=True)
    finally:
        if hasattr(pdf, "close"):
            pdf.close()


def _write_text_result(output_path: str, text: str) -> None:
    with open(output_path, "xb") as output_file:
        output_file.write(text.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Limits are set before importing pypdf, PDFium or Pillow."""
    _apply_resource_limits()
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation", choices=("text", "thumbnail"), required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-chars", type=int)
    args = parser.parse_args(argv)

    try:
        if args.operation == "text":
            if args.max_chars is None or args.max_chars < 0:
                return 2
            text = extract_pdf_text(args.input, max_chars=args.max_chars)
            _write_text_result(args.output, text)
        else:
            render_pdf_thumbnail(args.input, args.output)
    except MemoryError:
        return RESOURCE_LIMIT_EXIT_CODE
    except OSError as error:
        if error.errno == errno.EFBIG:
            return RESOURCE_LIMIT_EXIT_CODE
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
