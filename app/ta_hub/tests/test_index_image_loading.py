"""トップページの画像がファーストビューの描画を遅らせないことを確認する。"""

import re
from pathlib import Path

from django.test import SimpleTestCase

INDEX_TEMPLATE = Path(__file__).resolve().parent.parent / 'templates' / 'ta_hub' / 'index.html'


class IndexImageLoadingTest(SimpleTestCase):
    def test_all_images_are_lazy_loaded(self):
        # ヒーローは画像を持たないので、ポスター等を即時に読むと
        # 描画を止める CSS と帯域を取り合って LCP が遅れる
        tags = re.findall(r'<img\b[^>]*>', INDEX_TEMPLATE.read_text(encoding='utf-8'), re.S)
        self.assertTrue(tags)
        eager = [t for t in tags if 'loading="lazy"' not in t]
        self.assertEqual(eager, [])
