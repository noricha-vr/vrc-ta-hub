"""convert_markdown関数と_escape_unknown_html_tags関数のテスト"""
from django.test import TestCase

from event.libs import _escape_unknown_html_tags, convert_markdown


class TestEscapeUnknownHtmlTags(TestCase):
    """未知HTMLタグエスケープ機能のテスト"""

    def test_escape_unknown_tag(self):
        """P-AMI<Q>のような未知タグがエスケープされる"""
        result = _escape_unknown_html_tags("P-AMI<Q>")
        self.assertEqual(result, "P-AMI&lt;Q&gt;")

    def test_escape_unknown_tag_with_content(self):
        """<foo>bar</foo>のような未知タグがエスケープされる"""
        result = _escape_unknown_html_tags("<foo>bar</foo>")
        self.assertEqual(result, "&lt;foo&gt;bar&lt;/foo&gt;")

    def test_escape_unknown_tag_with_attributes(self):
        """属性付きの未知タグがエスケープされる"""
        result = _escape_unknown_html_tags('<custom attr="val">')
        self.assertEqual(result, '&lt;custom attr="val"&gt;')

    def test_escape_self_closing_tag(self):
        """自己閉じタグがエスケープされる"""
        result = _escape_unknown_html_tags("<custom/>")
        self.assertEqual(result, "&lt;custom/&gt;")

    def test_preserve_allowed_tags(self):
        """許可されたタグはエスケープされない"""
        result = _escape_unknown_html_tags("<strong>bold</strong>")
        self.assertEqual(result, "<strong>bold</strong>")

    def test_preserve_allowed_tag_with_attributes(self):
        """属性付きの許可されたタグはエスケープされない"""
        result = _escape_unknown_html_tags('<a href="https://example.com">link</a>')
        self.assertEqual(result, '<a href="https://example.com">link</a>')

    def test_preserve_code_block(self):
        """コードブロック内のタグはエスケープされない"""
        text = "```html\n<custom>tag</custom>\n```"
        result = _escape_unknown_html_tags(text)
        self.assertEqual(result, text)

    def test_preserve_inline_code(self):
        """インラインコード内のタグはエスケープされない"""
        result = _escape_unknown_html_tags("Use `<Q>` for query")
        self.assertEqual(result, "Use `<Q>` for query")

    def test_mixed_content(self):
        """許可タグと未知タグが混在する場合"""
        text = "<p>This is P-AMI<Q> and <strong>bold</strong></p>"
        result = _escape_unknown_html_tags(text)
        self.assertEqual(result, "<p>This is P-AMI&lt;Q&gt; and <strong>bold</strong></p>")

    def test_math_symbols_not_affected(self):
        """数学記号（a < b > c）は影響を受けない"""
        result = _escape_unknown_html_tags("a < b > c")
        self.assertEqual(result, "a < b > c")

    def test_incomplete_tag_not_affected(self):
        """不完全なタグは影響を受けない"""
        result = _escape_unknown_html_tags("<foo")
        self.assertEqual(result, "<foo")

    def test_multiple_unknown_tags(self):
        """複数の未知タグがエスケープされる"""
        result = _escape_unknown_html_tags("<ABC>と<DEF>と<xyz>")
        self.assertEqual(result, "&lt;ABC&gt;と&lt;DEF&gt;と&lt;xyz&gt;")


class TestConvertMarkdown(TestCase):
    """convert_markdown関数のテスト"""

    def test_unknown_tag_displayed(self):
        """Markdown変換後も未知タグがエスケープされて表示される"""
        html = convert_markdown("This is P-AMI<Q> technology")
        self.assertIn("P-AMI", html)
        self.assertIn("&lt;Q&gt;", html)

    def test_preserve_valid_markdown(self):
        """通常のMarkdownは正しく変換される"""
        html = convert_markdown("**bold** and *italic*")
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<em>italic</em>", html)

    def test_preserve_markdown_list(self):
        """リストは正しく変換される"""
        html = convert_markdown("- item1\n- item2")
        self.assertIn("<li>", html)
        self.assertIn("item1", html)
        self.assertIn("item2", html)

    def test_preserve_inline_code_in_markdown(self):
        """インラインコード内の未知タグは保持される"""
        html = convert_markdown("Use `P-AMI<Q>` syntax")
        self.assertIn("<code>", html)
        # コード内のタグはMarkdownライブラリによってエスケープされる
        self.assertIn("P-AMI", html)


class TestAllowedTagsAndAttributes(TestCase):
    """許可タグと属性のテスト"""

    def test_img_tag_preserved(self):
        """imgタグがサニタイズされずに保持される"""
        html = convert_markdown('<img src="/static/poster.png" alt="ポスター">')
        self.assertIn("<img", html)
        self.assertIn('src="/static/poster.png"', html)
        self.assertIn('alt="ポスター"', html)

    def test_img_tag_with_style_preserved(self):
        """imgタグのstyle属性が保持される"""
        html = convert_markdown('<img src="/test.png" style="max-width: 100%;">')
        self.assertIn('style="max-width: 100%;"', html)

    def test_button_tag_preserved_without_onclick(self):
        """buttonタグは保持されるが、onclick属性は除去される"""
        html = convert_markdown('<button onclick="alert()">Click</button>')
        self.assertIn("<button", html)
        self.assertIn("</button>", html)
        self.assertNotIn('onclick="alert()"', html)

    def test_button_tag_with_style_preserved_without_onclick(self):
        """buttonタグのstyle属性は保持され、onclick属性は除去される"""
        html = convert_markdown('<button style="background: blue;" onclick="test()">Test</button>')
        self.assertIn('style="background: blue;"', html)
        self.assertNotIn('onclick="test()"', html)

    def test_a_tag_download_attribute_preserved(self):
        """aタグのdownload属性が保持される"""
        html = convert_markdown('<a href="/file.png" download="poster.png">Download</a>')
        self.assertIn('download="poster.png"', html)

    def test_div_tag_style_preserved(self):
        """divタグのstyle属性が保持される"""
        html = convert_markdown('<div style="display: flex;">Content</div>')
        self.assertIn('style="display: flex;"', html)

    def test_combined_poster_html(self):
        """ポスター表示用のHTML（img, button, styleの組み合わせ）が保持される"""
        markdown = '''
<div style="text-align: center;">
    <img src="/static/poster.png" alt="ポスター" style="max-width: 100%;">
    <button onclick="downloadImage()" style="margin-top: 10px;">ダウンロード</button>
</div>
'''
        html = convert_markdown(markdown)
        self.assertIn("<img", html)
        self.assertIn("<button", html)
        self.assertIn('style="text-align: center;"', html)
        self.assertNotIn('onclick="downloadImage()"', html)

    def test_a_tag_with_javascript_href_removed(self):
        """aタグのjavascript:リンクはhrefから除去される"""
        html = convert_markdown('<a href="javascript:alert(1)">X</a>')
        self.assertIn("<a", html)
        self.assertNotIn('href="javascript:alert(1)"', html)


class TestYouTubeEmbed(TestCase):
    """YouTube埋め込み機能のテスト（2025年仕様対応）"""

    def test_youtube_url_converted_to_iframe(self):
        """YouTube URLがiframeに変換される"""
        html = convert_markdown("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIn("<iframe", html)
        self.assertIn("youtube.com/embed/dQw4w9WgXcQ", html)

    def test_youtube_short_url_converted_to_iframe(self):
        """YouTube短縮URLがiframeに変換される"""
        html = convert_markdown("https://youtu.be/dQw4w9WgXcQ")
        self.assertIn("<iframe", html)
        self.assertIn("youtube.com/embed/dQw4w9WgXcQ", html)

    def test_youtube_iframe_has_referrerpolicy(self):
        """YouTube iframeにreferrerpolicy属性が含まれる（2025年仕様）"""
        html = convert_markdown("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIn('referrerpolicy="strict-origin-when-cross-origin"', html)

    def test_youtube_iframe_has_allow_attribute(self):
        """YouTube iframeにallow属性が含まれる（2025年仕様）"""
        html = convert_markdown("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIn('allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"', html)

    def test_youtube_embed_container_class(self):
        """YouTube埋め込みがyoutube-embed-containerクラスでラップされる"""
        html = convert_markdown("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIn('class="youtube-embed-container"', html)

    def test_youtube_url_with_parameters(self):
        """パラメータ付きYouTube URLが正しく処理される"""
        html = convert_markdown("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=60")
        self.assertIn("<iframe", html)
        self.assertIn("youtube.com/embed/dQw4w9WgXcQ", html)
