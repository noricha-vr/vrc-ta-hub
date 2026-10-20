"""公開ページのサムネイル描画と静的画像 URL。"""
from __future__ import annotations

import json
import re
import threading
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from django.templatetags.static import static
from PIL import Image, ImageDraw, ImageFont

ASSET_ROOT = Path(__file__).resolve().parent / 'og_assets'
# 閉じ括弧・句読点・小書き文字などを行頭に置かない。
NO_LINE_START = frozenset('、。，．！？!?）)]｝}」』】〉》〕・ーぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮ')


def absolute_static_url(request, path: str) -> str:
    url = static(path)
    if url.startswith(('http://', 'https://', '//')):
        return url
    return request.build_absolute_uri('/' + url.lstrip('/'))


def _wrap_text(text: str, font, width: int) -> list[str]:
    """日本語は文字単位、収まる英数字の単語は単語単位で折り返す。"""
    tokens = re.findall(r"[A-Za-z0-9]+(?:[\-_'][A-Za-z0-9]+)*|[^\S\n]+|\n|.", text)
    # 禁則文字は手前の文字・単語と一緒に扱う。連続する閉じ括弧もまとめる。
    groups = []
    for token in tokens:
        if token[0] in NO_LINE_START and groups and not groups[-1].isspace():
            groups[-1] += token
        else:
            groups.append(token)
    lines, line = [], ''
    for group in groups:
        if group == '\n':
            lines.append(line.rstrip())
            line = ''
            continue
        pieces = [group]
        if font.getlength(group) > width:
            # パネルより長い単語だけ文字単位に分割する（末尾の禁則文字は残す）。
            pieces = []
            for char in group:
                if char in NO_LINE_START and pieces:
                    pieces[-1] += char
                else:
                    pieces.append(char)
        for piece in pieces:
            if line and font.getlength(line + piece) > width:
                lines.append(line.rstrip())
                line = ''
            line += piece if line else piece.lstrip()
    if line or not lines:
        lines.append(line.rstrip())
    return lines


def render_text_png(base: Path, title: str, asset_name: str = 'base') -> bytes:
    # 文字は公開済みの集会・ガイド・お知らせからのみ取得する。
    # リクエストの v はキーに含めず、同じ素材・文字の描画を使い回す。
    # lru_cache は描画中の結果を共有しないので、同時アクセスで同じ画像を何度も描かないよう 1 本ずつ通す。
    # 描画は 1 枚数十 ms で、キャッシュに載った後はロックをすぐ抜ける。
    with _render_lock:
        return _render_text_png_cached(str(base), title, asset_name)


_render_lock = threading.Lock()


# 描く文字は承認済みの集会・LT 受付中の集会・ガイド・お知らせの数までで、合計 200 件ほど。
# 順に叩かれても描き直しが続かないよう、全種類が載る大きさにする（1 枚約 0.5MB）。
@lru_cache(maxsize=256)
def _render_text_png_cached(base_dir: str, title: str, asset_name: str) -> bytes:
    base = Path(base_dir)
    with Image.open(base / f'{asset_name}.png') as source:
        image = source.convert('RGB')
    if image.size != (1200, 630):
        raise ValueError('Invalid image dimensions')
    config = json.loads((base / f'{asset_name}.json').read_text())
    x, y, width, height = config['text_box']
    if not (0 <= x < x + width <= 1200 and 0 <= y < y + height <= 630):
        raise ValueError('Invalid text box')
    # 任意の Unicode や改行を含むタイトルでも同じ描画経路を通す。
    title = title.replace('\r\n', '\n').replace('\r', '\n').replace('\t', ' ')
    # 収まる中で一番大きい文字にする。ただし最後の行が 1 文字だけになる組み方（「手\n順」など）は
    # 見栄えが悪いので、小さくして避けられる時は避ける。避けられなければ最初に収まった組み方を使う。
    fallback = None
    for size in range(58, 29, -2):
        font = ImageFont.truetype(str(ASSET_ROOT / 'fonts' / 'MochiyPopOne-Regular.ttf'), size)
        lines = _wrap_text(title, font, width)
        line_height = round(size * 1.3)
        if len(lines) <= 3 and size + (len(lines) - 1) * line_height <= height:
            if len(lines) == 1 or len(lines[-1].strip()) > 1:
                break
            fallback = fallback or (font, lines, line_height)
    else:
        if fallback is not None:
            font, lines, line_height = fallback
    if len(lines) > 3:
        lines = lines[:3]
        tail = lines[-1].rstrip()
        while tail and font.getlength(tail + '…') > width:
            tail = tail[:-1]
        lines[-1] = tail + '…'
    # ink の上下端を使い、フォント固有の余白によらずパネル内で上下中央にする。
    boxes = [font.getbbox(line or ' ') for line in lines]
    top = min(box[1] + i * line_height for i, box in enumerate(boxes))
    bottom = max(box[3] + i * line_height for i, box in enumerate(boxes))
    origin_y = y + (height - (bottom - top)) / 2 - top
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text((x, origin_y + i * line_height), line, font=font, fill=config['color'])
    output = BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()
