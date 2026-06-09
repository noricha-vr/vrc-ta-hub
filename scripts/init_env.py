"""`.env.local` 自動生成スクリプト.

`.env.example` を base にコピーし、SECRET_KEY / REQUEST_TOKEN / FERNET_KEY を
セキュアな乱数で自動生成する。新規開発者が初期セットアップで手書きする
手数を削減することが目的。

Usage:
    uv run scripts/init_env.py            # .env.local を生成
    uv run scripts/init_env.py --force    # 既存 .env.local を上書き
    uv run scripts/init_env.py --dry-run  # 標準出力にプレビューのみ
"""

from __future__ import annotations

import argparse
import base64
import secrets
import sys
from pathlib import Path

# SECRET_KEY: Django の SECRET_KEY 用。50 文字相当の token_urlsafe を使う。
SECRET_KEY_BYTES = 50
# REQUEST_TOKEN: バッチ処理認証用。32 文字相当で十分。
REQUEST_TOKEN_BYTES = 32

# 値が空のキーに付ける TODO コメント。手動入力すべきキーであることを明示する。
TODO_COMMENT = "# TODO: 値を設定してください"


def _generate_fernet_key() -> str:
    """Fernet 互換鍵 (urlsafe base64 32 bytes) を生成する.

    cryptography.fernet.Fernet.generate_key() と同一仕様だが、依存追加を避けて
    標準ライブラリのみで実装する。
    """
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


# 自動生成対象のキーと生成関数のマップ.
# value は引数なしで文字列を返す callable.
AUTO_GENERATED_KEYS: dict[str, callable] = {
    "SECRET_KEY": lambda: secrets.token_urlsafe(SECRET_KEY_BYTES),
    "REQUEST_TOKEN": lambda: secrets.token_urlsafe(REQUEST_TOKEN_BYTES),
    "FERNET_KEY": _generate_fernet_key,
}


def transform_line(line: str) -> str:
    """`.env.example` の 1 行を `.env.local` 用に変換する.

    - コメント行・空行はそのまま
    - `SECRET_KEY=` / `REQUEST_TOKEN=` は自動生成値を埋める
    - `KEY=value` (デフォルト値あり) はそのまま
    - `KEY=` (空) は末尾に TODO コメントを付与
    """
    stripped = line.rstrip("\n")

    # コメント行・空行はそのまま返す
    if not stripped.strip() or stripped.lstrip().startswith("#"):
        return line

    # KEY=VALUE 形式でないものはそのまま返す (堅牢性のため)
    if "=" not in stripped:
        return line

    key, value = stripped.split("=", 1)
    key_name = key.strip()

    # 自動生成対象キー
    if key_name in AUTO_GENERATED_KEYS:
        generated = AUTO_GENERATED_KEYS[key_name]()
        return f"{key}={generated}\n"

    # 値が空 (インラインコメントもなし) → TODO コメント付与
    if value.strip() == "":
        return f"{stripped}  {TODO_COMMENT}\n"

    # デフォルト値あり (インラインコメント含む) はそのまま
    return line


def generate_env_local(example_path: Path) -> str:
    """`.env.example` を読み、`.env.local` 用の文字列を返す."""
    with example_path.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    return "".join(transform_line(line) for line in lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=".env.example を base に .env.local を自動生成する",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="既存の .env.local を上書きする (デフォルトはエラー)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="標準出力にプレビューだけ表示し、ファイルは書き込まない",
    )
    args = parser.parse_args()

    # カレントディレクトリの .env.example / .env.local を対象とする
    cwd = Path.cwd()
    example_path = cwd / ".env.example"
    target_path = cwd / ".env.local"

    if not example_path.exists():
        print(f"ERROR: {example_path} が見つかりません", file=sys.stderr)
        return 1

    content = generate_env_local(example_path)

    if args.dry_run:
        sys.stdout.write(content)
        return 0

    if target_path.exists() and not args.force:
        print(
            f"ERROR: {target_path} は既に存在します。"
            "上書きするには --force を指定してください",
            file=sys.stderr,
        )
        return 1

    target_path.write_text(content, encoding="utf-8")
    print(f"生成しました: {target_path}")
    print("次のステップ: 'TODO' コメントが付いているキー (API キー類) を手動で埋めてください")
    return 0


if __name__ == "__main__":
    sys.exit(main())
