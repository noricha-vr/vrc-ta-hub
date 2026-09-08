#!/usr/bin/env python3
"""本番DBのdotenvをデータとして読み、必要な4変数だけを子プロセスへ渡す。"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

DB_KEYS = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


class ConfigurationError(ValueError):
    """秘密値を含まない設定エラー。"""


def read_db_env(path: Path) -> dict[str, str]:
    """単一行の値を読む。単引用符はliteral、二重引用符はCompose互換。"""
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise ConfigurationError("環境ファイルをUTF-8で読み込めません") from None
    values: dict[str, str] = {}
    for line in text.split("\n"):
        match = ASSIGNMENT.fullmatch(line)
        if not match or match[1] not in DB_KEYS:
            continue
        key, raw = match[1], match[2].strip()
        if key in values:
            raise ConfigurationError(f"{key} が重複しています")
        if raw.startswith("'"):
            if len(raw) < 2 or not raw.endswith("'") or "'" in raw[1:-1]:
                raise ConfigurationError(f"{key} の単引用符が不正です")
            value = raw[1:-1]
        elif raw.startswith('"'):
            try:
                value = json.loads(raw)
            except (ValueError, TypeError):
                raise ConfigurationError(f"{key} の二重引用符が不正です") from None
            value = value.replace("$$", "$")
        else:
            value = re.split(r"\s+#", raw, maxsplit=1)[0].rstrip()
            if any(c in value for c in ("'", '"', "\\")):
                raise ConfigurationError(f"{key} は引用符で囲んでください")
        if not value or any(c in value for c in ("\0", "\r", "\n")):
            raise ConfigurationError(f"{key} が空か制御文字を含みます")
        if "op://" in value:
            raise ConfigurationError(f"{key} のop参照を環境ファイル内の実値へ復元してください")
        values[key] = value
    missing = [key for key in DB_KEYS if key not in values]
    if missing:
        raise ConfigurationError("必須変数がありません: " + ", ".join(missing))
    return values


def main(argv: list[str]) -> int:
    if len(argv) < 3 or (argv[2:] != ["--check"] and (argv[2] != "--" or len(argv) < 4)):
        print("Usage: production_db_env.py ENV_FILE --check | -- COMMAND [ARGS...]", file=sys.stderr)
        return 2
    try:
        values = read_db_env(Path(argv[1]))
    except ConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if argv[2:] == ["--check"]:
        return 0
    environment = os.environ.copy()
    environment.pop("OP_SERVICE_ACCOUNT_TOKEN", None)
    environment.update(values)
    try:
        os.execvpe(argv[3], argv[3:], environment)
    except OSError:
        print("ERROR: DB操作コマンドを起動できません", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
