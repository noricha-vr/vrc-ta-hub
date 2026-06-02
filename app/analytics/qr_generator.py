"""QR コード PNG 画像生成ヘルパー。

`qrcode` ライブラリで URL を PNG に変換し、`ImageField.save()` にそのまま渡せる
`ContentFile` を返す。印刷想定で 1辺 ~410px（box_size=10, border=4）の十分なサイズ。
"""
import io
import uuid

import qrcode
from django.core.files.base import ContentFile


def generate_qr_png(url: str, *, box_size: int = 10, border: int = 4) -> ContentFile:
    """指定 URL の QR コード PNG を生成して ContentFile で返す。

    Args:
        url: QR にエンコードする URL。
        box_size: 1モジュールのピクセル数。10 で約 410px 四方になる。
        border: 周囲の余白（モジュール数）。仕様上 4 以上が必須。

    Returns:
        ImageField.save(name, file) に渡せる ContentFile。name は uuid.hex.png。
    """
    qr = qrcode.QRCode(box_size=box_size, border=border)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return ContentFile(buf.getvalue(), name=f'{uuid.uuid4().hex}.png')
