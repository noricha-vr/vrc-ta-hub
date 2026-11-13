"""docs/guide の本文に合わせて docs/guide/_lastmod.json を更新する。

使い方: ガイドの md を変えたら、リポジトリのルートで
    cd app && python ../scripts/update_guide_lastmod.py
本文が変わったページだけ、日付が今日になる。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'app'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')

import django  # noqa: E402

django.setup()

from guide.lastmod import refresh  # noqa: E402

records = refresh()
print(f'{len(records)} ページを記録しました')
