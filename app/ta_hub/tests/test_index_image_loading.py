"""トップページの画像がファーストビューの描画を遅らせないことを確認する。"""

import re
from pathlib import Path

from django.test import SimpleTestCase

INDEX_TEMPLATE = Path(__file__).resolve().parent.parent / 'templates' / 'ta_hub' / 'index.html'
# ここより下のセクションはモバイルの初期表示に入らない
BELOW_FOLD_MARKER = '<!-- special events section (Vket期間中'
LAZY_EXCEPT_FIRST = '{% if not forloop.first %}loading="lazy" {% endif %}'


def _img_tags(html):
    return re.findall(r'<img\b.*?>', html, re.S)


class IndexImageLoadingTest(SimpleTestCase):
    def setUp(self):
        html = INDEX_TEMPLATE.read_text(encoding='utf-8')
        self.assertIn(BELOW_FOLD_MARKER, html)
        self.above, self.below = html.split(BELOW_FOLD_MARKER, 1)

    def test_below_fold_images_are_lazy(self):
        # 画面外のポスター等を即時に読むと、描画を止める CSS と帯域を取り合って LCP が遅れる
        tags = _img_tags(self.below)
        self.assertTrue(tags)
        self.assertEqual([t for t in tags if 'loading="lazy"' not in t], [])

    def test_first_images_near_hero_are_not_lazy(self):
        # ヒーロー直下に来うる画像は、セクション先頭の 1 枚を即時に読む
        tags = _img_tags(self.above)
        self.assertTrue(tags)
        unconditional_lazy = [
            t for t in tags if 'loading="lazy"' in t and LAZY_EXCEPT_FIRST not in t
        ]
        self.assertEqual(unconditional_lazy, [])
        self.assertTrue(any(LAZY_EXCEPT_FIRST in t for t in tags))
