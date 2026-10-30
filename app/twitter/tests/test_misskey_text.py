"""Misskey 本文での誤メンションと利用者入力の装飾解釈を防ぐテスト。"""

from django.test import SimpleTestCase

from twitter.misskey_text import to_misskey_text


class MisskeyTextTest(SimpleTestCase):
    def test_speaker_account_becomes_x_link(self):
        self.assertEqual(
            to_misskey_text('発表者さん（@some_user） #VRChat'),
            '発表者さん（https://x.com/some_user） #VRChat',
        )

    def test_mentions_outside_speaker_format_are_neutralized(self):
        text = to_misskey_text('@local @remote@example.com 名前さん（@foo）')
        self.assertNotIn('@', text)
        self.assertIn('https://x.com/foo', text)
        self.assertIn('＠remote＠example.com', text)

    def test_mfm_functions_and_tags_are_neutralized(self):
        text = to_misskey_text('$[x2 集会] <center>発表</center> <b>@foo</b>')
        self.assertNotIn('$[', text)
        self.assertNotIn('<', text)
        self.assertNotIn('>', text)
        self.assertNotIn('@', text)
        self.assertIn('集会', text)
        self.assertIn('発表', text)

    def test_urls_and_hashtags_are_preserved(self):
        url = 'https://example.com/@user?q=hello#section'
        self.assertEqual(to_misskey_text(f'#VRChat #技術 {url}'), f'#VRChat #技術 {url}')

    def test_markup_after_x_link_is_still_neutralized(self):
        text = to_misskey_text('名前さん（@foo）$[x2 <center>@other</center>]')
        self.assertNotIn('$[', text)
        self.assertNotIn('<', text)
        self.assertNotIn('@', text)

    def test_plain_text_and_empty_text_are_preserved(self):
        for text in ('', '技術・学術系イベントの発表です\n#VRChat'):
            with self.subTest(text=text):
                self.assertEqual(to_misskey_text(text), text)
