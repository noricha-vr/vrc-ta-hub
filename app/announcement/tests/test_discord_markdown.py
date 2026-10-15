"""Discord プレビューの対応書式と、入力を HTML として扱わないことを確かめる。"""
from html import escape
from html.parser import HTMLParser
from unittest import TestCase

from django.template import Context, Engine
from django.utils.safestring import SafeString, mark_safe

from announcement.discord_markdown import render_discord_markdown


class ParsedHTML(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


class DiscordMarkdownTests(TestCase):
    def test_headings_and_subtext(self):
        html = render_discord_markdown('# 大見出し\n## 見出し\n### 小見出し\n-# 補足')

        self.assertIn('<h2 class="discord-heading-1">大見出し</h2>', html)
        self.assertIn('<h3 class="discord-heading-2">見出し</h3>', html)
        self.assertIn('<h4 class="discord-heading-3">小見出し</h4>', html)
        self.assertIn('<div class="discord-subtext">補足</div>', html)
        self.assertNotIn('<h1', html)

    def test_inline_styles(self):
        cases = {
            '**太字**': '<strong>太字</strong>',
            '*斜体*': '<em>斜体</em>',
            '_斜体_': '<em>斜体</em>',
            '__下線__': '<u>下線</u>',
            '***太字斜体***': '<strong><em>太字斜体</em></strong>',
            '~~取り消し線~~': '<s>取り消し線</s>',
            '||秘密||': '<span class="discord-spoiler" tabindex="0" aria-label="スポイラー">秘密</span>',
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertIn(expected, render_discord_markdown(source))

    def test_nested_styles(self):
        self.assertIn('<strong>太字と<em>斜体</em></strong>', render_discord_markdown('**太字と*斜体***'))
        self.assertIn('<u><strong>太字</strong>と<em>斜体</em></u>', render_discord_markdown('__**太字**と_斜体___'))
        self.assertIn('<strong>太字と<em>斜体</em>の続き</strong>', render_discord_markdown('**太字と*斜体*の続き**'))
        self.assertIn('<em>斜体と<strong>太字</strong>の続き</em>', render_discord_markdown('*斜体と**太字**の続き*'))
        self.assertIn('<em>斜体と<u>下線</u>の続き</em>', render_discord_markdown('_斜体と__下線__の続き_'))

    def test_single_and_multiline_quotes(self):
        html = render_discord_markdown('> 引用\n> **続き**\n通常\n>>> 以降\n\n最後')

        self.assertIn('<blockquote><div class="discord-line">引用</div><div class="discord-line"><strong>続き</strong></div></blockquote>', html)
        self.assertIn('</blockquote><div class="discord-line">通常</div><blockquote>', html)
        self.assertTrue(html.endswith('<div class="discord-line">最後</div></blockquote>'))
        self.assertEqual(html.count('<blockquote>'), 2)

    def test_unordered_ordered_and_nested_lists(self):
        html = render_discord_markdown('- 親\n  * 子\n    - 孫は同じ段\n- 次\n1. 一\n2. 二')

        self.assertIn('<ul><li>親<ul><li>子</li><li>孫は同じ段</li></ul></li><li>次</li></ul>', html)
        self.assertIn('<ol start="1"><li>一</li><li>二</li></ol>', html)
        self.assertIn('<ul><li>項目</li></ul>', render_discord_markdown('* 項目'))
        self.assertIn('<ul><li>親<ol start="3"><li>子</li></ol></li></ul>', render_discord_markdown('- 親\n  3. 子'))

    def test_fenced_code_is_escaped_and_not_formatted(self):
        source = '```python\n# 見出し\n**太字** <@123> https://a.example/\n<script>alert(1)</script>\n```'
        html = render_discord_markdown(source)

        self.assertEqual(html, '<pre><code># 見出し\n**太字** &lt;@123&gt; https://a.example/\n&lt;script&gt;alert(1)&lt;/script&gt;\n</code></pre>')
        self.assertNotIn('<a ', html)
        self.assertIn('<pre><code>**未完了**</code></pre>', render_discord_markdown('```\n**未完了**'))

    def test_code_language_is_never_used_as_an_attribute(self):
        html = render_discord_markdown('```x" onmouseover="alert(1)\n安全\n```')

        self.assertEqual(html, '<pre><code>安全\n</code></pre>')

    def test_inline_code_is_not_formatted(self):
        html = render_discord_markdown('`**太字** <@123> <script> https://a.example/` **外側**')

        self.assertIn('<code>**太字** &lt;@123&gt; &lt;script&gt; https://a.example/</code>', html)
        self.assertIn('<strong>外側</strong>', html)
        self.assertNotIn('<a ', html)

    def test_links_and_angle_links(self):
        html = render_discord_markdown('[**案内**](https://a.example/path_(x)?a=1&b=2) http://b.example/ <https://c.example/>')
        links = [attrs for tag, attrs in ParsedHTML(html).tags if tag == 'a']

        self.assertEqual([link['href'] for link in links], ['https://a.example/path_(x)?a=1&b=2', 'http://b.example/', 'https://c.example/'])
        for link in links:
            self.assertEqual(link['target'], '_blank')
            self.assertEqual(link['rel'], 'noopener noreferrer nofollow')
        self.assertIn('<strong>案内</strong></a>', html)
        self.assertNotIn('&lt;https://c.example/', html)

    def test_autolinks_do_not_consume_formatting_or_surrounding_punctuation(self):
        html = render_discord_markdown('**https://a.example/** (https://b.example/x).')

        self.assertIn('</a></strong>', html)
        self.assertIn('href="https://b.example/x"', html)
        self.assertIn('</a>).', html)

    def test_mentions_and_custom_emoji(self):
        html = render_discord_markdown('<@123> <@!456> <@&789> <#012> <:smile:345> <a:dance:678>')

        self.assertIn('title="ID: 123">@ユーザー</span>', html)
        self.assertIn('title="ID: 456">@ユーザー</span>', html)
        self.assertIn('title="ID: 789">@ロール</span>', html)
        self.assertIn('title="ID: 012">#チャンネル</span>', html)
        self.assertEqual(html.count('class="discord-mention"'), 4)
        self.assertIn(':smile: :dance:', html)

    def test_backslash_escapes(self):
        source = r'\*太字ではない\* \_斜体ではない\_ \~\~取消なし\~\~ \|\|秘密なし\|\| \`コードなし\` \[案内](https://a.example/)'
        html = render_discord_markdown(source)

        for tag in ('<em>', '<s>', '<span', '<code>'):
            self.assertNotIn(tag, html)
        self.assertIn('*太字ではない* _斜体ではない_ ~~取消なし~~ ||秘密なし|| `コードなし` [案内]', html)
        self.assertIn('# 見出しではない', render_discord_markdown(r'\# 見出しではない'))
        self.assertNotIn('discord-mention', render_discord_markdown(r'\<@123>'))
        self.assertIn('\\a', render_discord_markdown(r'\a'))

    def test_html_is_always_escaped_even_if_marked_safe(self):
        source = '<script>alert("x")</script><img src=x onerror=alert(1)><b>文字</b>'
        html = render_discord_markdown(mark_safe(source))

        self.assertIsInstance(html, SafeString)
        self.assertEqual(html, '<div class="discord-line">' + escape(source, quote=True) + '</div>')
        self.assertEqual([tag for tag, _ in ParsedHTML(html).tags], ['div'])

    def test_unsafe_link_schemes_remain_literal(self):
        for source in ('[x](javascript:alert(1))', '[x](data:text/html,test)', '[x](//a.example/)', '<javascript:alert(1)>'):
            with self.subTest(source=source):
                html = render_discord_markdown(source)
                self.assertEqual(html, '<div class="discord-line">' + escape(source, quote=True) + '</div>')
                self.assertNotIn('<a ', html)

    def test_link_attribute_injection_is_escaped(self):
        sources = (
            '[x](https://a.example/"onmouseover=alert(1))',
            '[x](https://a.example/&#34;onmouseover=alert(1))',
            '[<img src=x onerror=alert(1)>](https://a.example/)',
            '<https://a.example/"onclick=alert(1)>',
            'https://a.example/"onmouseover=alert(1)',
        )
        for source in sources:
            with self.subTest(source=source):
                html = render_discord_markdown(source)
                for tag, attrs in ParsedHTML(html).tags:
                    self.assertIn(tag, ('div', 'a'))
                    self.assertFalse(any(key.startswith('on') for key in attrs))
                    if tag == 'a':
                        self.assertEqual(set(attrs), {'href', 'target', 'rel'})
                        self.assertTrue(attrs['href'].startswith('https://'))
                self.assertNotIn('<img', html)

    def test_unsupported_markup_stays_literal(self):
        sources = ('#### 見出し', '#空白なし', '本文 # 見出しではない', '| 列1 | 列2 |\n| --- | --- |\n| 値1 | 値2 |', '![画像](https://a.example/image.png)', '<b>HTML</b>')
        for source in sources:
            with self.subTest(source=source):
                html = render_discord_markdown(source)
                expected = ''.join('<div class="discord-line">' + escape(line, quote=True) + '</div>' for line in source.split('\n'))
                self.assertEqual(html, expected)

    def test_newlines_blank_lines_and_spaces_are_preserved(self):
        html = render_discord_markdown('\n一行目\n\n  空白  あり\n')

        self.assertEqual(html, '<div class="discord-line"><br></div><div class="discord-line">一行目</div><div class="discord-line"><br></div><div class="discord-line">  空白  あり</div><div class="discord-line"><br></div>')
        self.assertEqual(render_discord_markdown('一\r\n\r\n二'), render_discord_markdown('一\n\n二'))

    def test_incomplete_delimiters_and_deep_input_are_safe(self):
        self.assertIn('**未完了', render_discord_markdown('**未完了'))
        source = ('**__~~||*_' * 160)[:2000]
        self.assertIsInstance(render_discord_markdown(source), SafeString)

    def test_template_filter_returns_safe_preview(self):
        engine = Engine(libraries={'discord_markdown': 'announcement.templatetags.discord_markdown'})
        template = engine.from_string('{% load discord_markdown %}{{ body|discord_markdown }}')

        html = template.render(Context({'body': '**太字** <script>x</script>'}))

        self.assertIn('<strong>太字</strong>', html)
        self.assertIn('&lt;script&gt;x&lt;/script&gt;', html)
        self.assertNotIn('&lt;strong&gt;', html)
