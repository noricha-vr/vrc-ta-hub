#!/usr/bin/env python3
"""限定credentialだけを渡す専用containerでlive smokeを実行する。"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_DUMMY_PREFIXES = ("dummy", "test", "changeme", "placeholder")
_SAFE_HOST_ENV = ("PATH", "HOME", "USER", "TMPDIR", "DOCKER_HOST", "DOCKER_CONTEXT")
_CALENDAR_CREDENTIAL_TARGET = "/run/secrets/google-calendar-credentials.json"


class LiveSmokeConfigurationError(ValueError):
    """live smokeを安全に起動できない設定を表す。"""


@dataclass(frozen=True)
class LiveSmokeProfile:
    """profileごとのcredential allowlistと既定テストを保持する。"""

    required_env: tuple[str, ...]
    default_labels: tuple[str, ...]
    credential_file_env: str | None = None


PROFILES = {
    "openrouter": LiveSmokeProfile(
        required_env=("OPENROUTER_API_KEY",),
        default_labels=(
            "event.tests.test_recurrence_rule_generation."
            "TestRecurrenceRuleGeneration.test_recurrence_preview_api_for_custom_rule",
            "event.tests.test_recurrence_preview_api."
            "TestRecurrencePreviewAPI.test_recurrence_preview_with_custom_rule",
            "event.tests.test_recurrence_llm_generation.TestRecurrenceLLMGeneration",
            "event.tests.test_generate_blog.TestGenerateBlog.test_generate_blog_pdf_only",
        ),
    ),
    "youtube": LiveSmokeProfile(
        required_env=("GOOGLE_API_KEY",),
        default_labels=(
            "event.tests.test_generate_blog.TestGenerateBlog.test_get_transcript",
        ),
    ),
    "blog-generation": LiveSmokeProfile(
        required_env=("OPENROUTER_API_KEY", "GOOGLE_API_KEY"),
        default_labels=(
            "event.tests.test_generate_blog.TestGenerateBlog.test_generate_blog_video_and_pdf",
            "event.tests.test_generate_blog.TestGenerateBlog.test_generate_blog_video_only",
            "event.tests.test_generate_blog.TestGenerateBlog.test_generate_blog_format_stability",
        ),
    ),
    "google-calendar": LiveSmokeProfile(
        required_env=("GOOGLE_CALENDAR_ID", "GOOGLE_CALENDAR_CREDENTIALS"),
        default_labels=("event.tests.test_google_calendar",),
        credential_file_env="GOOGLE_CALENDAR_CREDENTIALS",
    ),
}


def _parse_value(raw_value: str, *, name: str) -> str:
    value = raw_value.strip()
    if value.startswith(("'", '"')):
        quote = value[0]
        if len(value) < 2 or value[-1] != quote:
            raise LiveSmokeConfigurationError(f"{name} has an unterminated quoted value")
        value = value[1:-1]
    else:
        value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    if "\x00" in value or "\n" in value or "\r" in value:
        raise LiveSmokeConfigurationError(f"{name} contains an unsupported control character")
    return value


def _read_allowed_env_file(path: Path, allowed_names: set[str]) -> dict[str, str]:
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_LINE.match(line)
        if not match or match.group(1) not in allowed_names:
            continue
        name = match.group(1)
        values[name] = _parse_value(match.group(2), name=name)
    return values


def _is_real_value(value: str) -> bool:
    normalized = value.strip().lower()
    return bool(normalized) and not normalized.startswith(_DUMMY_PREFIXES)


def _load_profile_values(
    profile: LiveSmokeProfile,
    environ: Mapping[str, str],
    repo_root: Path,
) -> dict[str, str]:
    source_path = Path(environ.get("LIVE_SMOKE_ENV_FILE", repo_root / ".env.local"))
    file_values = _read_allowed_env_file(source_path, set(profile.required_env))
    values = {
        name: environ.get(name, "").strip() or file_values.get(name, "").strip()
        for name in profile.required_env
    }
    invalid = [name for name, value in values.items() if not _is_real_value(value)]
    if invalid:
        joined = ", ".join(invalid)
        raise LiveSmokeConfigurationError(f"missing or dummy credential: {joined}")
    return values


def _resolve_credential_file(value: str, repo_root: Path) -> Path:
    configured_path = Path(value).expanduser()
    if configured_path.is_absolute() and configured_path.parts[:2] == ("/", "app"):
        configured_path = repo_root / "app" / Path(*configured_path.parts[2:])
    elif not configured_path.is_absolute():
        configured_path = repo_root / "app" / configured_path
    resolved = configured_path.resolve()
    if not resolved.is_file():
        raise LiveSmokeConfigurationError("Google Calendar credential file does not exist")
    return resolved


def build_execution(
    profile_name: str,
    labels: Sequence[str],
    *,
    environ: Mapping[str, str],
    repo_root: Path = REPO_ROOT,
) -> tuple[list[str], dict[str, str]]:
    """secretをargvへ含めず、profile限定のDocker実行内容を組み立てる。"""

    try:
        profile = PROFILES[profile_name]
    except KeyError as exc:
        available = ", ".join(PROFILES)
        raise LiveSmokeConfigurationError(
            f"unknown live smoke profile: {profile_name}; available: {available}"
        ) from exc

    values = _load_profile_values(profile, environ, repo_root)
    child_env = {name: environ[name] for name in _SAFE_HOST_ENV if environ.get(name)}
    child_env["RUN_LIVE_SMOKE_TESTS"] = "1"
    child_env.update(values)

    command = [
        "docker",
        "compose",
        "--project-name",
        "vrc-ta-hub-live-smoke",
        "--project-directory",
        str(repo_root),
        "-f",
        str(repo_root / "docker-compose.live-smoke.yml"),
        "run",
        "--rm",
        "--no-deps",
        "--build",
        "-e",
        "RUN_LIVE_SMOKE_TESTS",
    ]

    for name in profile.required_env:
        command.extend(("-e", name))

    if profile.credential_file_env:
        credential_path = _resolve_credential_file(
            values[profile.credential_file_env],
            repo_root,
        )
        child_env[profile.credential_file_env] = _CALENDAR_CREDENTIAL_TARGET
        command.extend(
            ("--volume", f"{credential_path}:{_CALENDAR_CREDENTIAL_TARGET}:ro")
        )

    selected_labels = tuple(labels) or profile.default_labels
    command.extend(
        (
            "live-smoke",
            "python",
            "manage.py",
            "test",
            *selected_labels,
            "--tag=live_smoke",
            "--noinput",
            "--verbosity=1",
        )
    )
    return command, child_env


def main(argv: Sequence[str] | None = None) -> int:
    """CLI引数を検証し、隔離したlive smoke containerを起動する。"""

    parser = argparse.ArgumentParser(
        description="Run a live smoke profile with only its allowlisted credentials.",
    )
    parser.add_argument("profile", help=f"one of: {', '.join(PROFILES)}")
    parser.add_argument("labels", nargs="*", help="optional Django test labels")
    args = parser.parse_args(argv)

    try:
        command, child_env = build_execution(
            args.profile,
            args.labels,
            environ=os.environ,
        )
    except LiveSmokeConfigurationError as exc:
        print(f"live smoke configuration error: {exc}", file=sys.stderr)
        return 2

    completed = subprocess.run(command, cwd=REPO_ROOT, env=child_env, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
