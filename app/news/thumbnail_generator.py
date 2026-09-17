"""お知らせサムネイル（1200×630）の描画。

Pillow だけで完結し Django に依存しない（`scripts/generate_news_thumbnails.py` から
`django.setup()` 無しで使えるようにするため）。定義は `news.thumbnail_specs` が持つ。

一覧カードは 16:9 枠に `object-fit: cover` で表示するため、左右が約 40px ずつ
切り落とされる。文字は左右 100px のマージン内に収める。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .thumbnail_specs import (
    CATEGORY_THUMBNAIL_SPECS,
    POST_THUMBNAIL_SPECS,
    accent_for,
    label_for,
)

# キャンバス
IMAGE_WIDTH = 1200
IMAGE_HEIGHT = 630
MARGIN_X = 100
CONTENT_WIDTH = IMAGE_WIDTH - MARGIN_X * 2

# 配色
BACKGROUND_COLOR = "#F5F8FD"
CARD_FILL_COLOR = "#FFFFFF"
CARD_OUTLINE_COLOR = "#E3EAF5"
TITLE_COLOR = "#17213A"
CHIP_TEXT_COLOR = "#FFFFFF"
SITE_NAME_COLOR = "#4A5A73"
DIVIDER_COLOR = "#E3EAF5"

# 上端のアクセント帯
ACCENT_BAR_HEIGHT = 14

# 白カード
CARD_BOX = (40, 40, IMAGE_WIDTH - 40, IMAGE_HEIGHT - 40)
CARD_RADIUS = 28
CARD_OUTLINE_WIDTH = 2

# カテゴリチップ
CHIP_TOP = 92
CHIP_HEIGHT = 56
CHIP_PADDING_X = 28
CHIP_FONT_SIZE = 32

# タイトル
TITLE_TOP = 180
TITLE_BOTTOM = 470
TITLE_MAX_FONT_SIZE = 76
TITLE_MIN_FONT_SIZE = 44
TITLE_FONT_STEP = 4
TITLE_LINE_HEIGHT_RATIO = 1.4
MAX_TITLE_LINES = 3
ELLIPSIS = "…"

# カテゴリ既定画像はタイトル位置にカテゴリ名を大きく置く
CATEGORY_TITLE_MAX_FONT_SIZE = 112

# フッター（区切り線・ロゴ・サイト名）
DIVIDER_Y = 500
DIVIDER_WIDTH = 2
FOOTER_TOP = 516
LOGO_SIZE = 64
SITE_NAME = "VRChat技術・学術系イベントHub"
SITE_NAME_FONT_SIZE = 34
SITE_NAME_GAP = 24

# 禁則処理（行頭に来られない文字 / 行末に来られない文字）
LEADING_PROHIBITED = "、。，．」』）】〉》！？ー・：；"
TRAILING_PROHIBITED = "「『（【〈《"

FONT_PATH = Path(__file__).resolve().parent / "thumbnail_assets" / "fonts" / "NotoSansJP-Bold.otf"
LOGO_PATH = Path(__file__).resolve().parent.parent / "site" / "android-chrome-512x512.png"


class ThumbnailAssetError(RuntimeError):
    """同梱アセット（フォント・ロゴ）が見つからない。"""


def font_path() -> Path:
    """同梱フォントのパスを返す。"""
    if not FONT_PATH.exists():
        raise ThumbnailAssetError(
            f"サムネイル用フォントが見つかりません: {FONT_PATH}"
            "（app/news/thumbnail_assets/README.md の手順で再取得してください）"
        )
    return FONT_PATH


def logo_path() -> Path:
    """サイトロゴのパスを返す。"""
    if not LOGO_PATH.exists():
        raise ThumbnailAssetError(f"サイトロゴが見つかりません: {LOGO_PATH}")
    return LOGO_PATH


def load_font(size: int) -> ImageFont.FreeTypeFont:
    """指定サイズの同梱フォントを返す。"""
    return ImageFont.truetype(str(font_path()), size)


def _is_katakana(char: str) -> bool:
    """カタカナ（長音符 ー を含む）か。カタカナ語の途中で折り返さないための判定。"""
    return "\u30a1" <= char <= "\u30fc"


def _tokenize(text: str) -> list[str]:
    """ASCII 連続・カタカナ連続・空白・その他の和文 1 文字を単位に分割する。

    ASCII 語とカタカナ語は語中で折り返すと読みにくいので 1 トークンにまとめる。
    """
    tokens: list[str] = []
    buffer = ""
    buffer_kind = ""  # "ascii" / "katakana" / ""

    def flush() -> None:
        nonlocal buffer, buffer_kind
        if buffer:
            tokens.append(buffer)
        buffer = ""
        buffer_kind = ""

    for char in text:
        if char.isspace():
            flush()
            tokens.append(" ")
        elif char.isascii():
            if buffer_kind != "ascii":
                flush()
            buffer += char
            buffer_kind = "ascii"
        elif _is_katakana(char):
            if buffer_kind != "katakana":
                flush()
            buffer += char
            buffer_kind = "katakana"
        else:
            flush()
            tokens.append(char)
    flush()
    return tokens


def _merge_leading_prohibited(tokens: list[str]) -> list[str]:
    merged: list[str] = []
    for token in tokens:
        if merged and token[0] in LEADING_PROHIBITED and merged[-1] != " ":
            merged[-1] += token
        else:
            merged.append(token)
    return merged


def _merge_trailing_prohibited(tokens: list[str]) -> list[str]:
    merged: list[str] = []
    pending = ""
    for token in tokens:
        token = pending + token
        pending = ""
        if token[-1] in TRAILING_PROHIBITED:
            pending = token
            continue
        merged.append(token)
    if pending:
        merged.append(pending)
    return merged


def apply_kinsoku(tokens: list[str]) -> list[str]:
    """禁則文字を隣のトークンへ結合し、折り返し不可の単位にまとめる。"""
    return _merge_trailing_prohibited(_merge_leading_prohibited(tokens))


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """禁則を守って `max_width` 以内に折り返す。"""
    lines: list[str] = []
    current = ""
    for token in apply_kinsoku(_tokenize(text)):
        if not current and token == " ":
            continue
        candidate = current + token
        if current and font.getlength(candidate.rstrip()) > max_width:
            lines.append(current.rstrip())
            current = "" if token == " " else token
        else:
            current = candidate
    if current.strip():
        lines.append(current.rstrip())
    return lines or [""]


def _truncate_line(line: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    """末尾を削って `…` 付きで max_width に収める。"""
    if font.getlength(line) <= max_width:
        return line
    truncated = line
    while truncated and font.getlength(truncated + ELLIPSIS) > max_width:
        truncated = truncated[:-1]
    return truncated + ELLIPSIS


def _clamp_lines(
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int,
) -> list[str]:
    """行数・行幅の上限に収める（超過分は `…` で打ち切る）。"""
    clamped = [_truncate_line(line, font, max_width) for line in lines[:max_lines]]
    if len(lines) > max_lines and clamped:
        last = clamped[-1]
        if not last.endswith(ELLIPSIS):
            clamped[-1] = _truncate_line(last + ELLIPSIS, font, max_width)
    return clamped


def line_height_for(font_size: int) -> int:
    """行送り（px）を返す。"""
    return int(font_size * TITLE_LINE_HEIGHT_RATIO)


def fit_title(
    text: str,
    max_width: int = CONTENT_WIDTH,
    max_height: int = TITLE_BOTTOM - TITLE_TOP,
    max_font_size: int = TITLE_MAX_FONT_SIZE,
    min_font_size: int = TITLE_MIN_FONT_SIZE,
    max_lines: int = MAX_TITLE_LINES,
) -> tuple[list[str], ImageFont.FreeTypeFont]:
    """行数・幅・高さに収まる最大のフォントサイズで折り返し結果を返す。"""
    for size in range(max_font_size, min_font_size - 1, -TITLE_FONT_STEP):
        font = load_font(size)
        lines = wrap_text(text, font, max_width)
        fits_width = all(font.getlength(line) <= max_width for line in lines)
        fits_height = line_height_for(size) * len(lines) <= max_height
        if len(lines) <= max_lines and fits_width and fits_height:
            return lines, font

    font = load_font(min_font_size)
    lines = _clamp_lines(wrap_text(text, font, max_width), font, max_width, max_lines)
    return lines, font


def _draw_background(image: Image.Image, accent: str) -> ImageDraw.ImageDraw:
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, IMAGE_WIDTH, ACCENT_BAR_HEIGHT), fill=accent)
    draw.rounded_rectangle(
        CARD_BOX,
        radius=CARD_RADIUS,
        fill=CARD_FILL_COLOR,
        outline=CARD_OUTLINE_COLOR,
        width=CARD_OUTLINE_WIDTH,
    )
    return draw


def _draw_chip(draw: ImageDraw.ImageDraw, label: str, accent: str) -> None:
    font = load_font(CHIP_FONT_SIZE)
    width = int(font.getlength(label)) + CHIP_PADDING_X * 2
    box = (MARGIN_X, CHIP_TOP, MARGIN_X + width, CHIP_TOP + CHIP_HEIGHT)
    draw.rounded_rectangle(box, radius=CHIP_HEIGHT // 2, fill=accent)
    draw.text(
        (MARGIN_X + CHIP_PADDING_X, CHIP_TOP + CHIP_HEIGHT // 2),
        label,
        font=font,
        fill=CHIP_TEXT_COLOR,
        anchor="lm",
    )


def _draw_title(draw: ImageDraw.ImageDraw, title: str, max_font_size: int) -> None:
    band_height = TITLE_BOTTOM - TITLE_TOP
    lines, font = fit_title(title, max_font_size=max_font_size)
    line_height = line_height_for(font.size)
    top = TITLE_TOP + (band_height - line_height * len(lines)) // 2
    for index, line in enumerate(lines):
        center_y = top + line_height * index + line_height // 2
        draw.text((MARGIN_X, center_y), line, font=font, fill=TITLE_COLOR, anchor="lm")


def _draw_footer(image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    draw.line(
        (MARGIN_X, DIVIDER_Y, IMAGE_WIDTH - MARGIN_X, DIVIDER_Y),
        fill=DIVIDER_COLOR,
        width=DIVIDER_WIDTH,
    )
    with Image.open(logo_path()) as source:
        logo = source.convert("RGBA").resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
    image.paste(logo, (MARGIN_X, FOOTER_TOP), logo)
    draw.text(
        (MARGIN_X + LOGO_SIZE + SITE_NAME_GAP, FOOTER_TOP + LOGO_SIZE // 2),
        SITE_NAME,
        font=load_font(SITE_NAME_FONT_SIZE),
        fill=SITE_NAME_COLOR,
        anchor="lm",
    )


def render_thumbnail(
    title: str,
    accent: str,
    chip_label: str | None = None,
    max_font_size: int = TITLE_MAX_FONT_SIZE,
) -> Image.Image:
    """1200×630 のサムネイル画像を組み立てて返す。"""
    image = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), BACKGROUND_COLOR)
    draw = _draw_background(image, accent)
    if chip_label:
        _draw_chip(draw, chip_label, accent)
    _draw_title(draw, title, max_font_size)
    _draw_footer(image, draw)
    return image


def save_thumbnail(image: Image.Image, destination: Path) -> Path:
    """PNG（メタ情報なし・最適化あり）で保存する。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", optimize=True)
    return destination


CATEGORY_JOB_PREFIX = "category:"


@dataclass(frozen=True)
class ThumbnailJob:
    """1 枚ぶんの生成指示（定義 + 描画パラメータ）。"""

    name: str
    filename: str
    title: str
    accent: str
    chip_label: str | None
    max_font_size: int


def build_jobs() -> list[ThumbnailJob]:
    """記事 7 枚 + カテゴリ既定 2 枚の生成指示を返す。"""
    jobs = [
        ThumbnailJob(
            name=spec.slug,
            filename=spec.filename,
            title=spec.title,
            accent=accent_for(spec.category_slug),
            chip_label=label_for(spec.category_slug),
            max_font_size=TITLE_MAX_FONT_SIZE,
        )
        for spec in POST_THUMBNAIL_SPECS
    ]
    jobs.extend(
        ThumbnailJob(
            name=f"{CATEGORY_JOB_PREFIX}{spec.category_slug}",
            filename=spec.filename,
            title=spec.label,
            accent=accent_for(spec.category_slug),
            chip_label=None,
            max_font_size=CATEGORY_TITLE_MAX_FONT_SIZE,
        )
        for spec in CATEGORY_THUMBNAIL_SPECS
    )
    return jobs
