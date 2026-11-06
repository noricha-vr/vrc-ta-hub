"""ガイドページの最終更新日(sitemap.xml の lastmod 用)。

本番イメージには .git が無く、ファイルの更新時刻はビルド時刻になって全ページ同じ日付になる。
そのため「本文のハッシュと、そのハッシュになった日」を docs/guide/_lastmod.json に記録しておく。
本文を変えたら `python scripts/update_guide_lastmod.py` を実行する(ハッシュが変わったページだけ日付が今日になる)。
記録が本文とずれていると test_lastmod が落ちるので、更新し忘れは CI で止まる。
"""
import hashlib
import json
from datetime import date
from pathlib import Path

from guide.rendering import is_public_path


def _guide_root() -> Path:
    from guide.views import GUIDE_ROOT
    return GUIDE_ROOT


def lastmod_file(root: Path | None = None) -> Path:
    return (root or _guide_root()) / '_lastmod.json'


def content_hash(file: Path) -> str:
    return hashlib.sha256(file.read_bytes()).hexdigest()[:16]


def public_pages(root: Path | None = None) -> dict[str, Path]:
    """公開するガイド md の {path: ファイル}。内部資料(_ 始まり・AGENTS)は除く。"""
    root = root or _guide_root()
    pages = {}
    for file in sorted(root.rglob('*.md')):
        path = file.relative_to(root).with_suffix('').as_posix()
        if is_public_path(path):
            pages[path] = file
    return pages


def load_records(root: Path | None = None) -> dict[str, dict]:
    file = lastmod_file(root)
    if not file.is_file():
        return {}
    return json.loads(file.read_text(encoding='utf-8'))


def lastmod_for(path: str, records: dict[str, dict]) -> str | None:
    record = records.get(path)
    return record['date'] if record else None


def latest_lastmod(records: dict[str, dict]) -> str | None:
    dates = [record['date'] for record in records.values()]
    return max(dates) if dates else None


def refresh(root: Path | None = None, today: date | None = None, initial_dates: dict[str, str] | None = None) -> dict[str, dict]:
    """記録を本文に合わせる。ハッシュが変わった(または新規の)ページだけ日付を更新する。"""
    root = root or _guide_root()
    today_text = (today or date.today()).isoformat()
    old = load_records(root)
    new = {}
    for path, file in public_pages(root).items():
        digest = content_hash(file)
        if path in old and old[path]['hash'] == digest:
            new[path] = old[path]
        else:
            new[path] = {'hash': digest, 'date': (initial_dates or {}).get(path, today_text)}
    lastmod_file(root).write_text(json.dumps(new, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return new
