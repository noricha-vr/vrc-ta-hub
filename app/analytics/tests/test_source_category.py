"""流入元の6分類を検証する。"""
from django.test import SimpleTestCase

from analytics.source_category import classify_source


class SourceCategoryTest(SimpleTestCase):
    def test_source_category_production_examples(self):
        examples = {
            'google / organic': 'search',
            '(direct) / (none)': 'direct',
            't.co / referral': 'sns',
            'yahoo / organic': 'search',
            'bing / organic': 'search',
            'chatgpt.com / ai-assistant': 'ai',
            'wolframcloud.com / referral': 'other',
            'poster / qr': 'other',
            'youtube.com / referral': 'sns',
            'github.com / referral': 'other',
            'misskey.io / referral': 'sns',
            'note.com / referral': 'other',
            '(not set)': 'other',
            '(data not available)': 'other',
            'vrc-ta-hub.com / referral': 'internal',
        }
        for source, expected in examples.items():
            with self.subTest(source=source):
                self.assertEqual(classify_source(source), expected)

    def test_source_category_sns_domains_and_subdomains(self):
        for source in (
            'x.com', 'twitter.com', 'bsky.app', 'facebook', 'www.facebook.com',
            'instagram', 'l.instagram.com', 'discord.com', 'discord.gg', 'youtu.be',
        ):
            with self.subTest(source=source):
                self.assertEqual(classify_source(f'{source} / referral'), 'sns')

    def test_source_category_ai_sources_and_medium(self):
        for source in (
            'chatgpt.com', 'perplexity.ai', 'www.perplexity.ai', 'claude.ai',
            'gemini', 'gemini.google.com', 'copilot', 'copilot.microsoft.com',
        ):
            with self.subTest(source=source):
                self.assertEqual(classify_source(f'{source} / referral'), 'ai')
        self.assertEqual(classify_source('new-assistant / ai-assistant'), 'ai')

    def test_source_category_normalization_and_medium_priority(self):
        self.assertEqual(classify_source(' Google / ORGANIC '), 'search')
        self.assertEqual(classify_source('chatgpt.com / organic'), 'search')
        self.assertEqual(classify_source('vrc-ta-hub.com / qr'), 'other')
        self.assertEqual(classify_source('www.vrc-ta-hub.com / referral'), 'internal')
        self.assertEqual(classify_source('not-twitter.com / referral'), 'other')
        self.assertEqual(classify_source(''), 'other')
