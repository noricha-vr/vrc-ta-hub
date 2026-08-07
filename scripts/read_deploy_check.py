#!/usr/bin/env python3
"""Read deploy-check.toml and print a normalized summary.

deploy-watch skill が生 TOML を目視で解釈するのを避け、スキーマ違反を実行前に
落とすための読み取り層。

vrc-ta-hub 固有の要件として `[migrations]` を必ず要約に含める。未適用 migration の
確認はこのプロジェクトのデプロイ事故（DatabaseCache のテーブル未作成で全ページ 500）を
防ぐ中心的な手段なので、読み捨てると設定ファイルを置く意味が無くなる。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any


CHECK_SECTIONS = ("critical", "important", "conditional", "internal", "static_integrity")

# log-based metric 名は gcloud の識別子制約に合わせる
METRIC_NAME_RE = re.compile(r"[A-Za-z0-9_]{1,100}")
# log_check 名は gcloud のフラグ引数へ渡るため、引用符・バックスラッシュ・制御文字を拒否する
# （バックスラッシュを許すとエスケープで引用符を持ち込めてしまう）
LOG_CHECK_NAME_RE = re.compile(r"[^\"'`\\\x00-\x1f\x7f]{1,200}")

# check_command は read-only でなければならない。deploy-watch は切替前にこれを実行するため、
# migrate が混ざると「確認のつもりで適用してしまう」事故になる。
# 許可する区切りを列挙する方式は `;migrate` `&&migrate` `(migrate)` を取りこぼすため、
# 「前後が識別子の一部でない」ことだけを条件にする。これで showmigrations / sqlmigrate や
# create_migrate_job.sh のようにファイル名へ含まれる場合は検知しない。
MIGRATE_TOKEN_RE = re.compile(r"(?<![\w./-])migrate(?![\w-])")


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    if not isinstance(data, dict):
        raise ValueError("TOML root must be a table")
    return data


def _matches_watch_path(check: dict[str, Any], watch_path: str | None) -> bool:
    patterns = [str(value) for value in _ensure_list(check.get("watch_path_contains"))]
    if not patterns:
        return True
    if not watch_path:
        return False
    return any(pattern in watch_path for pattern in patterns)


def _matches_service(check: dict[str, Any], service_name: str | None) -> bool:
    allowed = [str(value) for value in _ensure_list(check.get("only_when_service_in"))]
    if not allowed:
        return True
    if not service_name:
        return False
    return service_name in allowed


def _normalize_checks(config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    checks = config.get("checks") or {}
    if not isinstance(checks, dict):
        raise ValueError("checks must be a table")

    normalized: dict[str, list[dict[str, Any]]] = {}
    for section in CHECK_SECTIONS:
        items = checks.get(section) or []
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            raise ValueError(f"checks.{section} must be an array of tables")
        # スキーマ違反の要素を黙って捨てると `critical: 0 checks` で成功終了し、
        # 本番確認を実行しないままデプロイが進む。設定ミスとして落とす。
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(
                    f"checks.{section} entries must be tables, got {type(item).__name__}"
                )
        normalized[section] = list(items)
    return normalized


def _select_checks(
    checks: dict[str, list[dict[str, Any]]],
    watch_path: str | None,
    service_name: str | None,
) -> dict[str, list[dict[str, Any]]]:
    selected: dict[str, list[dict[str, Any]]] = {}
    for section, items in checks.items():
        selected[section] = [
            item
            for item in items
            if _matches_watch_path(item, watch_path) and _matches_service(item, service_name)
        ]
    return selected


def resolve_migrations(config: dict[str, Any], base_dir: Path | None = None) -> dict[str, Any]:
    """Validate and return the [migrations] contract.

    未適用 migration の確認手段が欠けたり、確認のつもりで適用してしまう定義を
    実行前に落とす。base_dir を渡すと、参照するスクリプトの実在も確認する。
    """
    migrations = config.get("migrations")
    if not isinstance(migrations, dict):
        raise ValueError("migrations must be a table")

    for key in ("check_command", "apply_command"):
        value = migrations.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"migrations.{key} is required and must be a non-empty string")

    check_command = str(migrations["check_command"])
    if MIGRATE_TOKEN_RE.search(check_command):
        raise ValueError(
            "migrations.check_command must be read-only, but it runs migrate: "
            f"{check_command!r}"
        )
    # Job 名が `-migrate` で終わる（apply_command をコピペした形）と上の検知をすり抜ける。
    # 確認は showmigrations を実行するスクリプト経由に限る、という契約を明示する。
    if "jobs execute" in check_command:
        raise ValueError(
            "migrations.check_command must not execute a Cloud Run job directly; "
            f"use a read-only script instead: {check_command!r}"
        )

    if base_dir is not None:
        root = base_dir.resolve()
        for script in _referenced_scripts(check_command):
            target = (root / script).resolve()
            # `./scripts/../../x.sh` のようにリポジトリ外を指す参照は、
            # 実在してもこの検証の対象外なので設定ミスとして落とす。
            if not target.is_relative_to(root):
                raise ValueError(
                    f"migrations.check_command references a script outside the repository: {script}"
                )
            if not target.exists():
                raise ValueError(f"migrations.check_command references a missing script: {script}")

    return dict(migrations)


def _referenced_scripts(command: str) -> list[str]:
    """コマンド文字列から scripts/*.sh 形式の参照を拾う。

    `./scripts/x.sh` だけでなく `bash scripts/x.sh` や絶対パス指定も対象にする。
    拾えないと実在確認が黙ってスキップされ、タイポが検証をすり抜ける。
    """
    return re.findall(r"(?:^|[\s'\"=])\.?/?(?:[\w./-]*/)??(scripts/[\w./-]+\.sh)", command)


def _index_check_ids(checks: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """critical / important の id を索引する。重複と欠落は設定ミスとして落とす。"""
    indexed: dict[str, dict[str, Any]] = {}
    for section in ("critical", "important"):
        for item in checks.get(section, []):
            check_id = item.get("id")
            if not isinstance(check_id, str) or not check_id.strip():
                raise ValueError(f"checks.{section} entry requires a non-empty string id")
            if check_id in indexed:
                raise ValueError(f"duplicate check id: {check_id}")
            indexed[check_id] = item
    return indexed


def resolve_critical_paths(
    config: dict[str, Any], checks: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """critical_paths の check_id を実チェックへ解決する。

    棚卸しと実チェックが機械的に紐づいていないと、チェックを消しても棚卸しだけが
    残り「守られているつもり」になる。
    """
    indexed = _index_check_ids(checks)
    resolved: list[dict[str, Any]] = []
    for path in _ensure_list(config.get("critical_paths")):
        if not isinstance(path, dict):
            raise ValueError("critical_paths entries must be tables")
        check_id = path.get("check_id")
        if not check_id:
            raise ValueError(f"critical_paths {path.get('feature')!r} requires check_id")
        source = indexed.get(str(check_id))
        if source is None:
            raise ValueError(
                f"critical_paths {path.get('feature')!r} references unknown check_id: {check_id}"
            )
        resolved.append({**path, "check_url": source.get("url")})
    return resolved


def resolve_absence_alerts(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve absence_alerts by expanding their referenced log_checks filter.

    Fail fast when a referenced log_check name does not exist: silently
    degrading to an empty filter would turn a monitoring gap into a green run.
    """
    alerts = _ensure_list(config.get("absence_alerts"))
    log_checks = [item for item in _ensure_list(config.get("log_checks")) if isinstance(item, dict)]
    by_name = {str(item.get("name")): item for item in log_checks if item.get("name")}

    resolved: list[dict[str, Any]] = []
    for alert in alerts:
        if not isinstance(alert, dict):
            raise ValueError("absence_alerts entries must be tables")
        name = alert.get("log_check")
        if not name:
            raise ValueError("absence_alerts entry requires log_check")
        if not LOG_CHECK_NAME_RE.fullmatch(str(name)):
            raise ValueError(f"absence_alerts log_check name has invalid characters: {name!r}")
        source = by_name.get(str(name))
        if source is None:
            raise ValueError(f"absence_alerts references unknown log_check: {name}")
        log_filter = source.get("filter")
        if not log_filter:
            raise ValueError(f"log_check {name} has no filter to expand")
        metric_name = alert.get("metric_name")
        if not metric_name:
            raise ValueError(f"absence_alerts for {name} requires metric_name")
        if not METRIC_NAME_RE.fullmatch(str(metric_name)):
            raise ValueError(
                f"absence_alerts metric_name must match {METRIC_NAME_RE.pattern}: {metric_name!r}"
            )
        duration = alert.get("absence_duration_s")
        # bool は int のサブクラスなので type() で厳密に判定する
        if type(duration) is not int or duration <= 0:
            raise ValueError(
                f"absence_alerts for {name} requires positive integer absence_duration_s"
            )
        resolved.append(
            {
                **alert,
                "filter": log_filter,
                "log_check_kind": source.get("kind"),
                "log_check_description": source.get("description"),
            }
        )
    return resolved


def build_summary(
    config: dict[str, Any],
    watch_path: str | None = None,
    service_name: str | None = None,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    checks = _normalize_checks(config)
    selected_checks = _select_checks(checks, watch_path, service_name)
    return {
        "schema_version": config.get("schema_version"),
        "title": config.get("title"),
        "service": config.get("service"),
        "source_of_truth": config.get("source_of_truth", False),
        "complete_checklist": _ensure_list(config.get("complete_checklist")),
        "migrations": resolve_migrations(config, base_dir),
        "checks": checks,
        "selected_checks": selected_checks,
        "log_checks": _ensure_list(config.get("log_checks")),
        "absence_alerts": resolve_absence_alerts(config),
        "critical_paths": resolve_critical_paths(config, checks),
        "watch_path": watch_path,
        "service_name": service_name,
    }


def _format_check(check: dict[str, Any]) -> str:
    identifier = check.get("id") or check.get("name") or "(unnamed)"
    name = check.get("name")
    # static_integrity は起点がページなので url ではなく page_url を持つ
    url = check.get("url") or check.get("page_url")
    parts = [str(identifier)]
    if name and str(name) != str(identifier):
        parts.append(str(name))
    if url:
        parts.append(f"[{url}]")
    return " ".join(parts)


def render_text(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"title: {summary.get('title') or '-'}")
    lines.append(f"service: {summary.get('service') or '-'}")
    lines.append(f"schema_version: {summary.get('schema_version') or '-'}")
    lines.append(f"source_of_truth: {summary.get('source_of_truth')}")

    migrations = summary.get("migrations") or {}
    lines.append("migrations:")
    for key in ("check_command", "apply_command"):
        lines.append(f"  - {key} = {migrations.get(key)}")

    checklist = summary.get("complete_checklist") or []
    lines.append(f"complete_checklist: {len(checklist)} items")
    for item in checklist:
        lines.append(f"  - {item}")

    checks = summary.get("selected_checks") or {}
    for section in CHECK_SECTIONS:
        items = checks.get(section) or []
        lines.append(f"{section}: {len(items)} checks")
        for item in items:
            lines.append(f"  - {_format_check(item)}")

    log_checks = summary.get("log_checks") or []
    lines.append(f"log_checks: {len(log_checks)} entries")
    for item in log_checks:
        if isinstance(item, dict):
            kind = item.get("kind", "log")
            lines.append(f"  - {item.get('name', '(unnamed)')} [{kind}]")

    absence_alerts = summary.get("absence_alerts") or []
    lines.append(f"absence_alerts: {len(absence_alerts)} entries")
    for item in absence_alerts:
        if isinstance(item, dict):
            duration = item.get("absence_duration_s")
            lines.append(f"  - {item.get('metric_name')} <- {item.get('log_check')} ({duration}s)")

    critical_paths = summary.get("critical_paths") or []
    lines.append(f"critical_paths: {len(critical_paths)} entries")
    for item in critical_paths:
        if isinstance(item, dict):
            priority = item.get("priority", "?")
            feature = item.get("feature", "(unnamed)")
            lines.append(f"  - {priority} {feature}")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        default="docs/deploy-check.toml",
        help="Path to deploy-check TOML (default: docs/deploy-check.toml)",
    )
    parser.add_argument(
        "--watch-path",
        help="Filter conditional checks by WATCH_PATH",
    )
    parser.add_argument(
        "--service-name",
        help="Filter service-specific checks by service name",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = Path(args.path)
    config = _load_toml(path)
    # スクリプト参照はリポジトリルート基準で解決する。toml のパスから逆算すると
    # 別ディレクトリの toml を渡したときに基準がずれるため、このファイルの位置を使う。
    summary = build_summary(
        config,
        watch_path=args.watch_path,
        service_name=args.service_name,
        base_dir=Path(__file__).resolve().parent.parent,
    )

    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(summary))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileNotFoundError as exc:
        print(f"deploy-check file not found: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except tomllib.TOMLDecodeError as exc:
        print(f"failed to parse deploy-check TOML: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except ValueError as exc:
        print(f"invalid deploy-check contract: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
