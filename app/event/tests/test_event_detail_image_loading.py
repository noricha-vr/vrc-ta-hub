from pathlib import Path

from django.test import SimpleTestCase

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "event" / "detail.html"


class EventDetailImageLoadingTest(SimpleTestCase):
    """記事ページの LCP（サムネイル）を遅らせる読み込みが戻らないことを確かめる."""

    def setUp(self):
        self.template = TEMPLATE.read_text(encoding="utf-8")

    def _img_tag(self, marker):
        start = self.template.rindex("<img", 0, self.template.index(marker))
        return self.template[start:self.template.index(">", start) + 1]

    def test_does_not_load_duplicate_font_awesome(self):
        """base.html の 6.5.1 と重ねて別版の Font Awesome を描画ブロックで読まない."""
        self.assertNotIn("font-awesome/6.0.0-beta3", self.template)

    def test_thumbnail_offers_smaller_widths_and_stays_eager(self):
        tag = self._img_tag("event-detail-thumbnail img-fluid")
        for width in ("600", "800", "1200"):
            self.assertIn(f"cf_resize:'{width}' }}}} {width}w", tag)
        # 表示幅は Bootstrap の container 幅から gutter（24px）を引いた値
        self.assertIn(
            'sizes="(min-width: 1400px) 1296px, (min-width: 1200px) 1116px, (min-width: 992px) 936px, '
            '(min-width: 768px) 696px, (min-width: 576px) 516px, calc(100vw - 24px)"',
            tag,
        )
        self.assertIn('fetchpriority="high"', tag)
        self.assertNotIn('loading="lazy"', tag)

    def test_community_poster_below_article_is_lazy(self):
        tag = self._img_tag("のポスター")
        self.assertIn('loading="lazy"', tag)
