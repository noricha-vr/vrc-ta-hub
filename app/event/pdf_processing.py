"""PDF処理でアプリケーション層とworker間に共有する表示・抽出上限."""

MAX_PDF_TEXT_PAGES = 30

PDF_THUMBNAIL_MAX_RENDER_SCALE = 2.0
PDF_THUMBNAIL_MAX_LONG_EDGE_PX = 1600


def get_pdf_thumbnail_render_scale(page) -> float:
    """PDFページの長辺が上限を超えないレンダリング倍率を返す."""
    try:
        width, height = page.get_size()
        long_edge = max(float(width), float(height))
    except (AttributeError, TypeError, ValueError):
        return PDF_THUMBNAIL_MAX_RENDER_SCALE

    if long_edge <= 0:
        return PDF_THUMBNAIL_MAX_RENDER_SCALE

    return min(PDF_THUMBNAIL_MAX_RENDER_SCALE, PDF_THUMBNAIL_MAX_LONG_EDGE_PX / long_edge)
