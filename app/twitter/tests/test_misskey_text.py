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

    def test_all_mfm_opening_syntax_is_neutralized(self):
        cases = [
            ('[公式ログイン](https://evil.example/login)', '［公式ログイン］(https://evil.example/login)'),
            ('?[公式](https://evil.example)', '?［公式］(https://evil.example)'),
            ('***動く*** **太字** *斜体* __太字__ _斜体_ ~~取消~~',
             '＊＊＊動く＊＊＊ ＊＊太字＊＊ ＊斜体＊ ＿＿太字＿＿ ＿斜体＿ ～～取消～～'),
            ('`code` ```code```', '｀code｀ ｀｀｀code｀｀｀'),
            (r'\(x\) \[x\]', '＼(x＼) ＼［x＼］'),
            ('$[x2 hello]', '＄［x2 hello］'),
            ('<plain>@user</plain> <center>text</center>', '＜plain＞＠user＜/plain＞ ＜center＞text＜/center＞'),
            (':custom_emoji: :ABC123: 19:00', '：custom＿emoji： ：ABC123： 19:00'),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(to_misskey_text(source), expected)

    def test_mfm_url_boundaries_do_not_hide_markup(self):
        for separator in (')', '日本語', '（', '。'):
            with self.subTest(separator=separator):
                self.assertEqual(
                    to_misskey_text(f'https://example.com/{separator}$[x2 hello]'),
                    f'https://example.com/{separator}＄［x2 hello］',
                )
        self.assertEqual(to_misskey_text('https://example.com/.,$[x2 hello]'),
                         'https://example.com/.,$［x2 hello］')

    def test_hashtag_syntax_characters_and_url_characters_are_preserved(self):
        text = '#VRChat技術学術 #tech_event #タグ_@$*~`\\: https://example.com/a_b/@user?q=$x#tag'
        self.assertEqual(to_misskey_text(text), text)
        self.assertEqual(to_misskey_text('#tech_event[x]'), '#tech_event［x］')

    def test_url_trailing_punctuation_is_outside_preserved_url(self):
        # MFM does not include a trailing dot/comma in the URL; decoration after it stays inert.
        self.assertEqual(to_misskey_text('https://example.com/.,**bold**'),
                         'https://example.com/.,＊＊bold＊＊')
