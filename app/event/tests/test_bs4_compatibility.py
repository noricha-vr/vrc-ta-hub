"""BeautifulSoup 更新後も表・埋め込みの表示と属性制限を維持する。"""
import unittest

from bs4 import BeautifulSoup

from event.services.markdown_processor import (
    _apply_table_classes,
    _convert_youtube_anchors_to_iframes,
    _remove_disallowed_iframes,
    convert_markdown,
)


class BeautifulSoupCompatibilityTest(unittest.TestCase):
    def test_table_retains_existing_classes(self):
        soup = BeautifulSoup('<table class="custom compact"></table><table></table>', 'html.parser')
        _apply_table_classes(soup)
        tables = soup.find_all('table')
        self.assertEqual(tables[0]['class'], ['custom', 'compact', 'table', 'table-responsive'])
        self.assertEqual(tables[1]['class'], ['table', 'table-responsive'])

    def test_youtube_embed_keeps_fullscreen_and_container(self):
        soup = BeautifulSoup(convert_markdown('https://youtu.be/dQw4w9WgXcQ'), 'html.parser')
        iframe = soup.find('iframe')
        self.assertIsNotNone(iframe)
        self.assertEqual(iframe['src'], 'https://www.youtube.com/embed/dQw4w9WgXcQ')
        self.assertTrue(iframe.has_attr('allowfullscreen'))
        self.assertIn('youtube-embed-container', iframe.parent['class'])

    def test_non_string_anchor_href_is_not_converted(self):
        soup = BeautifulSoup('<a>link</a>', 'html.parser')
        soup.a['href'] = ['https://youtu.be/dQw4w9WgXcQ']
        _convert_youtube_anchors_to_iframes(soup)
        self.assertIsNone(soup.find('iframe'))

    def test_invalid_iframe_attributes_do_not_bypass_domain_filter(self):
        soup = BeautifulSoup(
            '<iframe></iframe><iframe src="https://evil.example/video"></iframe>'
            '<iframe src="https://www.youtube.com/embed/valid"></iframe>', 'html.parser'
        )
        soup.find('iframe')['src'] = ['https://www.youtube.com/embed/invalid']
        _remove_disallowed_iframes(soup)
        self.assertEqual(len(soup.find_all('iframe')), 1)
        self.assertEqual(soup.iframe['src'], 'https://www.youtube.com/embed/valid')
