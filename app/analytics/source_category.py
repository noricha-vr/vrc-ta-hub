"""参照元/メディアを主催者向けの6分類にまとめる。"""

SOURCE_CATEGORY_LABELS = {
    'search': '検索',
    'sns': 'SNS',
    'direct': '直接',
    'internal': 'Hub 内',
    'ai': 'AI',
    'other': 'その他',
}

_SNS_SOURCES = (
    't.co', 'x.com', 'twitter.com', 'misskey.io', 'bsky.app',
    'facebook.com', 'instagram.com', 'discord.com', 'discord.gg',
    'youtube.com', 'youtu.be',
)
_AI_SOURCES = (
    'chatgpt.com', 'perplexity.ai', 'claude.ai', 'gemini.google.com',
    'copilot.microsoft.com', 'copilot.com',
)


def classify_source(source_medium: str) -> str:
    """参照元/メディアを search / sns / direct / internal / ai / other に分類する。"""
    source, _, medium = source_medium.lower().partition('/')
    source, medium = source.strip(), medium.strip()
    if medium == 'organic':
        return 'search'
    if medium == 'ai-assistant' or any(
        source == domain or source.endswith('.' + domain) for domain in _AI_SOURCES
    ) or source in ('perplexity', 'gemini', 'copilot'):
        return 'ai'
    if source == '(direct)':
        return 'direct'
    if medium == 'referral' and 'vrc-ta-hub.com' in source:
        return 'internal'
    if any(
        source == domain or source.endswith('.' + domain) for domain in _SNS_SOURCES
    ) or source in ('facebook', 'instagram', 'twitter', 'discord', 'youtube'):
        return 'sns'
    return 'other'
