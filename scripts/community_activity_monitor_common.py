"""Shared primitives for the local VRC community activity monitor."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

import requests

try:
    import fcntl
except ImportError:  # pragma: no cover - macOS/Linux運用。Windowsは単一起動を前提。
    fcntl = None

XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"
DEFAULT_BASE_URL = "https://vrc-ta-hub.com"
DEFAULT_MODEL = "grok-4.5"
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_INACTIVE_DAYS = 90
DEFAULT_REQUIRED_CHECKS = 2
DEFAULT_MIN_CHECK_INTERVAL_DAYS = 7
DEFAULT_MAX_CHECK_GAP_DAYS = 35
DEFAULT_MIN_INACTIVE_CONFIDENCE = 0.75
DEFAULT_EXPLICIT_END_CONFIDENCE = 0.90
DEFAULT_STATE_FILE = Path.home() / ".local/state/vrc-ta-hub/community-activity.json"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
VALID_STATUSES = {"active", "explicitly_ended", "no_recent_evidence", "unknown"}
X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
DISCORD_WEBHOOK_HOSTS = {
    "discord.com", "www.discord.com", "discordapp.com", "www.discordapp.com"
}
X_HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,15}$")
X_RESERVED_PATHS = {
    "compose", "explore", "hashtag", "home", "i", "intent", "messages",
    "notifications", "search", "share",
}


@dataclass(frozen=True)
class Assessment:
    status: str
    confidence: float
    latest_activity_date: date | None
    summary: str
    evidence_urls: list[str]
    response_ids: list[str]
    search_calls: int
    cost_usd: float


@dataclass
class MonitorResult:
    community_id: int
    name: str
    status: str
    confidence: float
    streak_before: int
    streak_after: int
    action: str
    summary: str
    evidence_urls: list[str]
    search_calls: int = 0
    cost_usd: float = 0.0
    error: str = ""


class MonitorError(RuntimeError):
    pass


def request_with_retries(
    session: requests.Session,
    method: str,
    url: str,
    *,
    retries: int = 3,
    **kwargs,
) -> requests.Response:
    safe_url = redact_url_for_logs(url)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            last_error = exc
            delay = 2**attempt
        else:
            if response.status_code not in RETRYABLE_STATUS_CODES:
                try:
                    response.raise_for_status()
                except requests.RequestException as exc:
                    detail = response.text[:300].replace("\n", " ")
                    raise MonitorError(
                        f"{method} {safe_url} returned HTTP "
                        f"{response.status_code}: {detail}"
                    ) from exc
                return response

            last_error = MonitorError(
                f"{method} {safe_url} returned HTTP "
                f"{response.status_code}: {response.text[:300]}"
            )
            retry_after = response.headers.get("Retry-After")
            delay = (
                float(retry_after)
                if retry_after and retry_after.isdigit()
                else 2**attempt
            )

        if attempt < retries:
            time.sleep(min(delay, 30))

    raise MonitorError(str(last_error or f"{method} {safe_url} failed"))


def extract_output_text(data: dict[str, Any]) -> str:
    texts: list[str] = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if (
                content.get("type") == "output_text"
                and isinstance(content.get("text"), str)
            ):
                texts.append(content["text"])
    if not texts:
        raise MonitorError("xAI response did not contain output_text")
    return "\n".join(texts)


def parse_assessment_json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            raise MonitorError("xAI decision JSON was not found")
        try:
            value = json.loads(text[start:end])
        except json.JSONDecodeError as exc:
            raise MonitorError("xAI decision JSON was invalid") from exc
    if not isinstance(value, dict):
        raise MonitorError("xAI decision must be a JSON object")
    return value


def extract_x_citations(data: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for value in data.get("citations", []):
        normalized = normalize_x_url(value)
        if normalized and normalized not in urls:
            urls.append(normalized)
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            for annotation in content.get("annotations", []):
                normalized = normalize_x_url(annotation.get("url"))
                if normalized and normalized not in urls:
                    urls.append(normalized)
    return urls


def intersect_evidence_urls(
    model_urls: Any,
    citations: list[str],
) -> list[str]:
    if not isinstance(model_urls, list):
        return []

    citations_by_status_id = {
        status_id: url
        for url in citations
        if (status_id := extract_x_status_id(url))
    }
    verified: list[str] = []
    for value in model_urls:
        normalized = normalize_x_url(value)
        if not normalized:
            continue
        status_id = extract_x_status_id(normalized)
        # 活動・終了の根拠は個別投稿URLに限定する。プロフィールURLや
        # 検索URLは「その投稿を実際に読んだ」証明にならない。
        matched = citations_by_status_id.get(status_id) if status_id else None
        if matched and normalized not in verified:
            verified.append(normalized)
    return verified[:10]


def extract_x_search_call_count(data: dict[str, Any]) -> int:
    usage = data.get("server_side_tool_usage")
    if isinstance(usage, dict):
        try:
            count = int(usage.get("SERVER_SIDE_TOOL_X_SEARCH", 0))
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            return count
    return sum(
        1
        for item in data.get("output", [])
        if item.get("type") == "x_search_call"
    )


def extract_xai_cost_usd(data: dict[str, Any]) -> float:
    try:
        ticks = int(data.get("usage", {}).get("cost_in_usd_ticks", 0))
    except (AttributeError, TypeError, ValueError):
        return 0.0
    return max(ticks, 0) / 10_000_000_000


def normalize_x_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in X_HOSTS:
        return ""
    return urlunsplit(
        (
            "https",
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            parsed.query,
            "",
        )
    )


def extract_x_status_id(value: str) -> str:
    normalized = normalize_x_url(value)
    if not normalized:
        return ""
    segments = [
        segment
        for segment in urlsplit(normalized).path.split("/")
        if segment
    ]
    if len(segments) < 3 or segments[1].lower() != "status":
        return ""
    status_id = segments[2]
    return status_id if status_id.isdigit() else ""


def normalize_x_handle(value: Any) -> str:
    handle = str(value or "").strip().lstrip("@").lower()
    if handle in X_RESERVED_PATHS or not X_HANDLE_PATTERN.fullmatch(handle):
        return ""
    return handle


def normalize_hashtags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        hashtag = str(item).strip().lstrip("#")
        if (
            not hashtag
            or len(hashtag) > 100
            or any(character.isspace() for character in hashtag)
        ):
            continue
        if hashtag not in normalized:
            normalized.append(hashtag)
    return normalized[:10]


def redact_url_for_logs(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if host in DISCORD_WEBHOOK_HOSTS and parsed.path.startswith("/api/webhooks/"):
        return urlunsplit((parsed.scheme, parsed.netloc, "/api/webhooks/***", "", ""))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def is_discord_webhook_url(value: str) -> bool:
    parsed = urlsplit(value.strip())
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in DISCORD_WEBHOOK_HOSTS
        and parsed.path.startswith("/api/webhooks/")
    )


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "communities": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MonitorError(
                f"state file is invalid: {self.path}"
            ) from exc
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("communities", {}), dict)
        ):
            raise MonitorError(
                f"state file has an invalid schema: {self.path}"
            )
        value.setdefault("version", 1)
        value.setdefault("communities", {})
        return value

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8") as file_handle:
                json.dump(
                    state,
                    file_handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                file_handle.write("\n")
                file_handle.flush()
                os.fsync(file_handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()


def _reset_inactive_streak(saved: dict[str, Any]) -> None:
    saved["inactiveStreak"] = 0
    saved.pop("lastInactiveCheckAt", None)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int_list(name: str) -> list[int]:
    values: list[int] = []
    for item in os.getenv(name, "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            value = int(item)
        except ValueError:
            continue
        if value not in values:
            values.append(value)
    return values


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bounded_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, 0.0), 1.0)


def _parse_date(value: Any) -> date | None:
    if value in (None, "", "null"):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_text(value: str, max_length: int) -> str:
    return " ".join(value.split())[:max_length]


def _escape_discord(value: str) -> str:
    return value.replace("@", "@\u200b").replace("`", "ˋ")
