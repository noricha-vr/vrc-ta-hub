"""告知本文のうち、Discord が扱う書式だけを安全な HTML にする。"""
from __future__ import annotations

import re
import string
from html import escape, unescape

from django.utils.safestring import SafeString, mark_safe


_LIST = re.compile(r'^( *)([-*]|[0-9]{1,9}\.) (.*)$')
_ANGLE = re.compile(r'&lt;([^\n]*?)&gt;')
_MENTION = re.compile(r'(@!?|@&|#)([0-9]+)\Z')
_EMOJI = re.compile(r'a?:([A-Za-z0-9_]+):[0-9]+\Z')
_URL = re.compile(r'https?://[^\s<>`*|~]+')
_ENTITY = re.compile(r'&(?:[A-Za-z]+|#[0-9]+|#x[0-9a-fA-F]+);')
_STYLES = (
    ('***', '<strong><em>', '</em></strong>'),
    ('**', '<strong>', '</strong>'),
    ('__', '<u>', '</u>'),
    ('~~', '<s>', '</s>'),
    ('||', '<span class="discord-spoiler" tabindex="0" aria-label="スポイラー">', '</span>'),
    ('*', '<em>', '</em>'),
    ('_', '<em>', '</em>'),
)


def _anchor(url: str, label: str) -> str:
    # url と label はエスケープ済み。属性は引用符を含めて改めてエスケープする。
    href = escape(unescape(url), quote=True)
    return f'<a href="{href}" target="_blank" rel="noopener noreferrer nofollow">{label}</a>'


def _masked_link(text: str, start: int) -> tuple[str, str, int] | None:
    """括弧を含む URL も、対応する閉じ括弧までひとまとまりに読む。"""
    label_end = text.find('](', start + 1)
    if label_end < 0 or '[' in text[start + 1:label_end]:
        return None
    depth = 1
    index = label_end + 2
    while index < len(text):
        if text[index] == '\\':
            index += 2
            continue
        if text[index] == '(':
            depth += 1
        elif text[index] == ')':
            depth -= 1
            if depth == 0:
                # Discord と同じく、URL 内の \) などのエスケープを外してリンク先にする
                url = re.sub(r'\\([^\w\s])', r'\1', text[label_end + 2:index])
                return text[start + 1:label_end], url, index + 1
        index += 1
    return None


def _inline(text: str, start: int = 0, stop: str = '', depth: int = 0, links: bool = True) -> tuple[str, int, bool]:
    """生成した HTML を再解析せず、エスケープ済みの入力を左から読む。"""
    parts = []
    index = start
    while index < len(text):
        nested_pair = stop in ('*', '_') and text.startswith(stop * 2, index) and not text.startswith(stop * 3, index)
        if stop and not nested_pair and text.startswith(stop, index) and index > start and not text[index - 1].isspace():
            return ''.join(parts), index + len(stop), True

        char = text[index]
        if char == '\\' and index + 1 < len(text):
            entity = _ENTITY.match(text, index + 1)
            if entity and unescape(entity[0]) in string.punctuation:
                parts.append(entity[0])
                index = entity.end()
                continue
            if text[index + 1] in string.punctuation:
                parts.append(text[index + 1])
                index += 2
                continue

        if char == '`':
            run = re.match(r'`+', text[index:])[0]
            end = text.find(run, index + len(run))
            if end >= 0:
                parts.append('<code>' + text[index + len(run):end] + '</code>')
                index = end + len(run)
                continue

        if char == '[' or text.startswith('![', index):
            image = char == '!'
            match = _masked_link(text, index + int(image))
            if match:
                label, url, end = match
                if links and not image and re.fullmatch(r'https?://\S+', url):
                    parts.append(_anchor(url, _inline(label, depth=depth + 1, links=False)[0]))
                else:
                    parts.append(text[index:end])
                index = end
                continue

        angle = _ANGLE.match(text, index)
        if angle:
            content = angle[1]
            mention = _MENTION.fullmatch(unescape(content))
            emoji = _EMOJI.fullmatch(content)
            if mention:
                label = {'@': '@ユーザー', '@!': '@ユーザー', '@&': '@ロール', '#': '#チャンネル'}[mention[1]]
                parts.append(f'<span class="discord-mention" title="ID: {mention[2]}">{label}</span>')
            elif emoji:
                parts.append(f':{emoji[1]}:')
            elif links and re.fullmatch(r'https?://\S+', content):
                parts.append(_anchor(content, content))
            else:
                parts.append(angle[0])
            index = angle.end()
            continue

        url = _URL.match(text, index) if links else None
        if url:
            value = url[0].split('&lt;', 1)[0].split('&gt;', 1)[0]
            if stop:
                value = value.split(stop, 1)[0]
            value = value.rstrip('.,:!?')
            while value.endswith(')') and value.count(')') > value.count('('):
                value = value[:-1]
            parts.append(_anchor(value, value))
            index += len(value)
            continue

        if depth < 24:
            for marker, opening, closing in _STYLES:
                next_index = index + len(marker)
                if text.startswith(marker, index) and next_index < len(text) and not text[next_index].isspace():
                    content, end, matched = _inline(text, next_index, marker, depth + 1, links)
                    parts.append(opening + content + closing if matched else marker + content)
                    index = end
                    break
            else:
                parts.append(char)
                index += 1
            continue

        parts.append(char)
        index += 1
    return ''.join(parts), index, False


def _list_level(match: re.Match) -> int:
    return int(len(match[1]) >= 2)


def _render_list(lines: list[str], index: int, level: int) -> tuple[str, int]:
    first = _LIST.fullmatch(lines[index])
    ordered = first[2].endswith('.')
    tag = 'ol' if ordered else 'ul'
    start = f' start="{int(first[2][:-1])}"' if ordered else ''
    parts = [f'<{tag}{start}>']
    while index < len(lines):
        item = _LIST.fullmatch(lines[index])
        if not item or _list_level(item) < level or item[2].endswith('.') != ordered:
            break
        parts.append('<li>' + _inline(item[3])[0])
        index += 1
        while index < len(lines):
            child = _LIST.fullmatch(lines[index])
            if not child or _list_level(child) <= level:
                break
            nested, index = _render_list(lines, index, 1)
            parts.append(nested)
        parts.append('</li>')
    parts.append(f'</{tag}>')
    return ''.join(parts), index


def _blocks(lines: list[str], depth: int = 0) -> str:
    parts = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if re.fullmatch(r'```[^`]*', line):
            end = index + 1
            while end < len(lines) and not re.fullmatch(r'```[ \t]*', lines[end]):
                end += 1
            code = '\n'.join(lines[index + 1:end])
            if end < len(lines) and end > index + 1:
                code += '\n'
            parts.append('<pre><code>' + code + '</code></pre>')
            index = end + 1
            continue

        if depth < 24 and line.startswith('&gt;&gt;&gt; '):
            quoted = [line[len('&gt;&gt;&gt; '):], *lines[index + 1:]]
            parts.append('<blockquote>' + _blocks(quoted, depth + 1) + '</blockquote>')
            break
        if depth < 24 and line.startswith('&gt; '):
            quoted = []
            while index < len(lines) and lines[index].startswith('&gt; '):
                quoted.append(lines[index][len('&gt; '):])
                index += 1
            parts.append('<blockquote>' + _blocks(quoted, depth + 1) + '</blockquote>')
            continue

        item = _LIST.fullmatch(line)
        if item:
            rendered, index = _render_list(lines, index, _list_level(item))
            parts.append(rendered)
            continue

        heading = re.fullmatch(r'(#{1,3}) (.*)', line)
        if heading:
            level = len(heading[1])
            tag = f'h{level + 1}'
            parts.append(f'<{tag} class="discord-heading-{level}">' + _inline(heading[2])[0] + f'</{tag}>')
        elif line.startswith('-# '):
            parts.append('<div class="discord-subtext">' + (_inline(line[3:])[0] or '<br>') + '</div>')
        else:
            parts.append('<div class="discord-line">' + (_inline(line)[0] or '<br>') + '</div>')
        index += 1
    return ''.join(parts)


def render_discord_markdown(text: str) -> SafeString:
    """先に全文をエスケープし、固定したタグと http(s) リンクだけを生成する。"""
    escaped = escape(str(text), quote=True).replace('\r\n', '\n').replace('\r', '\n')
    return mark_safe(_blocks(escaped.split('\n')))
