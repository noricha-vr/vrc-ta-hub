"""トップのヒーロー（めいゆん入り）の構成を確認する。"""

from pathlib import Path

from django.test import SimpleTestCase

TA_HUB_DIR = Path(__file__).resolve().parent.parent
INDEX_TEMPLATE = TA_HUB_DIR / 'templates' / 'ta_hub' / 'index.html'
MASCOT = 'ta_hub/images/meiyun-wave.webp'


class IndexHeroTest(SimpleTestCase):
    def setUp(self):
        html = INDEX_TEMPLATE.read_text(encoding='utf-8')
        start = html.index('<section class="top-hero"')
        self.hero = html[start:html.index('<!-- hero end -->')]

    def test_copy_and_cta_are_kept(self):
        # キャッチコピーと集会一覧への導線は作り替え前のまま
        self.assertIn('ようこそ！<br>VRChat技術学術系<wbr>集会の世界へ！', self.hero)
        self.assertIn("{% url 'community:list' %}", self.hero)
        self.assertIn('VRChatで開催される多彩な技術学術系集会をお届けします', self.hero)
        self.assertIn('入門者から経験豊富な方まで、みんなが楽しめるイベントが満載です！', self.hero)
        self.assertIn('技術・学術系 集会一覧', self.hero)

    def test_mascot_image_exists_and_has_size(self):
        # 寸法を書いておかないと画像の読み込みでレイアウトがずれる
        self.assertIn(f"{{% static '{MASCOT}' %}}", self.hero)
        self.assertIn('width="418" height="465"', self.hero)
        self.assertIn('alt="', self.hero)
        self.assertTrue((TA_HUB_DIR / 'static' / MASCOT).is_file())
