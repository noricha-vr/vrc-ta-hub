"""E2E 検証環境の分離契約（許可リスト）を検証する管理コマンド。"""
from __future__ import annotations

import json
import os
import subprocess
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand

ALLOWED_DB_HOSTS = frozenset({"db", "127.0.0.1", "localhost"})
ALLOWED_DB_NAMES = frozenset({"vrc_ta_hub_e2e"})
ALLOWED_DB_USERS = frozenset({"e2e_user"})
ALLOWED_URL_HOSTS = frozenset({"127.0.0.1", "localhost", "storage", "vrc-ta-hub", "host.docker.internal"})
FORBIDDEN_FRAGMENTS = ("vrc-ta-hub.com", "r2.cloudflarestorage.com", "discord.com", "discordapp.com")
DUMMY_REQUIRED_KEYS = (
    "GOOGLE_API_KEY",
    "GOOGLE_CALENDAR_ID",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "X_API_KEY",
    "X_API_SECRET",
    "X_ACCESS_TOKEN",
    "X_ACCESS_TOKEN_SECRET",
    "DISCORD_BOT_TOKEN",
)


def _git_sha() -> str:
    env_sha = str(os.environ.get("E2E_HUB_GIT_SHA", "") or "").strip()
    if env_sha:
        return env_sha
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
    except Exception:
        return "unknown"


def _check_url_allowed(name: str, raw: str, violations: list[str]) -> None:
    value = (raw or "").strip()
    if not value:
        return
    lower = value.lower()
    for frag in FORBIDDEN_FRAGMENTS:
        if frag in lower:
            violations.append(f"{name}: forbidden production host fragment ({frag})")
            return
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in ALLOWED_URL_HOSTS:
        violations.append(f"{name}: host {parsed.hostname!r} (scheme={parsed.scheme!r}) not in E2E allowlist")


def validate_e2e_isolation() -> dict:
    violations: list[str] = []

    if not getattr(settings, "DEBUG", False):
        violations.append("DEBUG must be True in E2E mode")
    if not getattr(settings, "E2E_LOOPBACK_STORAGE", False):
        violations.append("E2E_LOOPBACK_STORAGE must be enabled in E2E mode")
    if not getattr(settings, "E2E_DISABLE_EXTERNAL_YOUTUBE", False):
        violations.append("E2E_DISABLE_EXTERNAL_YOUTUBE must be enabled in E2E mode")

    db_conf = settings.DATABASES.get("default", {})
    db_engine = str(db_conf.get("ENGINE", "") or "")
    db_host = str(db_conf.get("HOST", "") or "").strip()
    db_name = str(db_conf.get("NAME", "") or "").strip()
    db_user = str(db_conf.get("USER", "") or "").strip()
    is_memory_sqlite = "sqlite3" in db_engine and (":memory:" in db_name or "memorydb_" in db_name)
    if not is_memory_sqlite:
        if db_host not in ALLOWED_DB_HOSTS:
            violations.append(f"DATABASES.default.HOST={db_host!r} not in E2E allowlist")
        if db_name not in ALLOWED_DB_NAMES:
            violations.append(f"DATABASES.default.NAME={db_name!r} not in E2E allowlist")
        if db_user not in ALLOWED_DB_USERS:
            violations.append(f"DATABASES.default.USER={db_user!r} not in E2E allowlist")

    for name in (
        "AWS_S3_ENDPOINT_URL",
        "AWS_S3_PRESIGN_ENDPOINT_URL",
        "DISCORD_WEBHOOK_URL",
        "DISCORD_REPORT_WEBHOOK_URL",
        "DISCORD_ANNOUNCE_WEBHOOK_URL",
        "CSRF_TRUSTED_ORIGIN",
        "SITE_URL",
    ):
        _check_url_allowed(name, str(getattr(settings, name, "") or os.environ.get(name, "")), violations)

    custom_domain = str(getattr(settings, "AWS_S3_CUSTOM_DOMAIN", "") or os.environ.get("AWS_S3_CUSTOM_DOMAIN", "")).strip()
    if custom_domain:
        _check_url_allowed("AWS_S3_CUSTOM_DOMAIN", f"http://{custom_domain}", violations)

    pub_bucket = str(getattr(settings, "AWS_STORAGE_BUCKET_NAME", "") or "")
    priv_bucket = str(getattr(settings, "AWS_PRIVATE_STORAGE_BUCKET_NAME", "") or "")
    if not pub_bucket.startswith("vrc-ta-hub-e2e-") or not priv_bucket.startswith("vrc-ta-hub-e2e-"):
        violations.append(f"S3 buckets must use vrc-ta-hub-e2e-* prefix (got {pub_bucket!r}, {priv_bucket!r})")
    if pub_bucket == priv_bucket:
        violations.append("Public and private S3 buckets must be distinct")

    for key in DUMMY_REQUIRED_KEYS:
        val = str(os.environ.get(key, "") or "")
        if val and not val.startswith("e2e-dummy-"):
            violations.append(f"{key} must use e2e-dummy-* prefix in E2E mode (only OPENROUTER_API_KEY may be real)")

    return {
        "ok": len(violations) == 0,
        "hub_sha": _git_sha(),
        "violations": violations,
    }


class Command(BaseCommand):
    help = "E2E 検証環境が本番から完全分離されているか許可リストで検証する（違反時は exit 2）。"

    def handle(self, *args, **options):
        result = validate_e2e_isolation()
        self.stdout.write(json.dumps(result, ensure_ascii=False))
        if not result["ok"]:
            raise SystemExit(2)
