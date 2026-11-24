"""録画確認・トリミング/途中カット確定ビューの入力解析・EDL 組み立てヘルパー。"""
from __future__ import annotations

import copy
import json
import math
from typing import Any

from django.http import HttpRequest

from event.models import RecordingEditOrder, RecordingReviewItem
from event.services.recording_ops.edl import (
    EDL_CUT_ACTIONS,
    MAX_CUTS_COUNT,
    EDLValidationError,
    extract_adopted_duration_sec,
    summarize_cuts,
)
from event.services.recording_ops.errors import RecordingValidationError, validate_positive_revision

_MAX_JSON_PAYLOAD_BYTES = 262_144
_CUT_REQUIRED_KEYS = frozenset({"id", "start_sec", "end_sec", "kind", "label", "action"})
_CUT_ALLOWED_KEYS = _CUT_REQUIRED_KEYS | {"source", "confidence"}


def parse_int_revision(raw_value: Any, *, strict_json: bool) -> int:
    """expected_revision をフォーム/JSON に応じて厳格に整数検証する。"""
    if strict_json:
        validate_positive_revision(raw_value, "expected_revision")
        return int(raw_value)
    if not isinstance(raw_value, str):
        raise RecordingValidationError("expected_revision は正の整数で指定してください。")
    stripped = raw_value.strip()
    if not stripped or not stripped.isascii() or not stripped.isdigit():
        raise RecordingValidationError("expected_revision は正の整数で指定してください。")
    try:
        parsed = int(stripped)
    except ValueError as exc:
        raise RecordingValidationError("expected_revision の数値が大きすぎます。") from exc
    validate_positive_revision(parsed, "expected_revision")
    return parsed


def parse_finite_seconds(raw_value: Any, field_name: str, *, strict_json: bool) -> float:
    """秒数入力を有限浮動小数点数（小数第 3 位丸め）へ正規化する。"""
    if strict_json:
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise RecordingValidationError(f"{field_name} は数値で指定してください。")
        try:
            val = float(raw_value)
        except (ValueError, OverflowError) as exc:
            raise RecordingValidationError(f"{field_name} に不正な数値は指定できません。") from exc
    else:
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise RecordingValidationError(f"{field_name} を入力してください。")
        try:
            val = float(raw_value.strip())
        except (ValueError, OverflowError) as exc:
            raise RecordingValidationError(f"{field_name} は数値で指定してください。") from exc
    if not math.isfinite(val):
        raise RecordingValidationError(f"{field_name} に不正な数値は指定できません。")
    return round(val, 3)


def normalize_submitted_cuts(raw_cuts: Any) -> list[dict[str, Any]]:
    """送信された cuts[] 配列の型・キー・数値を検証し正規化済みリストを返す。"""
    if not isinstance(raw_cuts, list):
        raise RecordingValidationError("cuts は配列で指定してください。")
    if len(raw_cuts) > MAX_CUTS_COUNT:
        raise RecordingValidationError(f"cuts は {MAX_CUTS_COUNT} 件以下で指定してください。")

    normalized: list[dict[str, Any]] = []
    for idx, entry in enumerate(raw_cuts):
        prefix = f"cuts[{idx}]"
        if not isinstance(entry, dict):
            raise RecordingValidationError(f"{prefix} はオブジェクトで指定してください。")
        missing_keys = _CUT_REQUIRED_KEYS - set(entry.keys())
        if missing_keys:
            raise RecordingValidationError(
                f"{prefix} に必須キーがありません: {', '.join(sorted(missing_keys))}"
            )
        unknown_keys = set(entry.keys()) - _CUT_ALLOWED_KEYS
        if unknown_keys:
            raise RecordingValidationError(
                f"{prefix} に未対応のキーがあります: {', '.join(sorted(unknown_keys))}"
            )

        cut_id = entry["id"]
        if not isinstance(cut_id, str) or not cut_id.strip():
            raise RecordingValidationError(f"{prefix}.id は空でない文字列で指定してください。")

        start_sec = parse_finite_seconds(entry["start_sec"], f"{prefix}.start_sec", strict_json=True)
        end_sec = parse_finite_seconds(entry["end_sec"], f"{prefix}.end_sec", strict_json=True)

        kind = entry["kind"]
        if not isinstance(kind, str) or not kind.strip():
            raise RecordingValidationError(f"{prefix}.kind は空でない文字列で指定してください。")

        label = entry["label"]
        if not isinstance(label, str):
            raise RecordingValidationError(f"{prefix}.label は文字列で指定してください。")

        action = entry["action"]
        if not isinstance(action, str) or action not in EDL_CUT_ACTIONS:
            raise RecordingValidationError(
                f"{prefix}.action は pending / accept / reject のいずれかで指定してください。"
            )

        item: dict[str, Any] = {
            "id": cut_id,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "kind": kind,
            "label": label,
            "action": action,
        }
        if "source" in entry and entry["source"] is not None:
            source = entry["source"]
            if not isinstance(source, str):
                raise RecordingValidationError(f"{prefix}.source は文字列で指定してください。")
            item["source"] = source
        if "confidence" in entry and entry["confidence"] is not None:
            confidence = parse_finite_seconds(entry["confidence"], f"{prefix}.confidence", strict_json=True)
            if not (0.0 <= confidence <= 1.0):
                raise RecordingValidationError(f"{prefix}.confidence は 0.0 以上 1.0 以下で指定してください。")
            item["confidence"] = confidence

        normalized.append(item)

    return normalized


def extract_trim_and_cuts_submission(
    request: HttpRequest,
) -> tuple[int, float, float, list[dict[str, Any]] | None]:
    """JSON / Form POST から expected_revision, start_sec, end_sec, cuts を取り出す。"""
    if request.content_type == "application/json":
        if len(request.body) > _MAX_JSON_PAYLOAD_BYTES:
            raise RecordingValidationError("リクエスト JSON のサイズが大きすぎます。")
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise RecordingValidationError("リクエスト JSON の形式が不正です。") from exc
        if not isinstance(payload, dict):
            raise RecordingValidationError("JSON オブジェクトを指定してください。")
        rev = parse_int_revision(payload.get("expected_revision"), strict_json=True)
        if "edl_json" in payload and isinstance(payload["edl_json"], dict):
            edl_obj = payload["edl_json"]
            trim_obj = edl_obj.get("trim")
            if not isinstance(trim_obj, dict):
                raise RecordingValidationError("edl_json.trim を指定してください。")
            raw_start = trim_obj.get("start_sec")
            raw_end = trim_obj.get("end_sec")
            raw_cuts = edl_obj["cuts"] if "cuts" in edl_obj else None
        else:
            raw_start = payload.get("start_sec")
            raw_end = payload.get("end_sec")
            raw_cuts = payload["cuts"] if "cuts" in payload else None
        start_sec = parse_finite_seconds(raw_start, "開始位置（start_sec）", strict_json=True)
        end_sec = parse_finite_seconds(raw_end, "終了位置（end_sec）", strict_json=True)
        cuts = normalize_submitted_cuts(raw_cuts) if raw_cuts is not None else None
        return (rev, start_sec, end_sec, cuts)

    rev = parse_int_revision(request.POST.get("expected_revision"), strict_json=False)
    start_sec = parse_finite_seconds(request.POST.get("start_sec"), "開始位置（start_sec）", strict_json=False)
    end_sec = parse_finite_seconds(request.POST.get("end_sec"), "終了位置（end_sec）", strict_json=False)
    raw_cuts_json = request.POST.get("cuts_json")
    cuts: list[dict[str, Any]] | None = None
    if isinstance(raw_cuts_json, str) and raw_cuts_json.strip():
        if len(raw_cuts_json.encode("utf-8")) > _MAX_JSON_PAYLOAD_BYTES:
            raise RecordingValidationError("cuts_json のサイズが大きすぎます。")
        try:
            parsed_cuts = json.loads(raw_cuts_json)
        except (ValueError, RecursionError) as exc:
            raise RecordingValidationError("cuts_json の JSON 形式が不正です。") from exc
        cuts = normalize_submitted_cuts(parsed_cuts)
    return (rev, start_sec, end_sec, cuts)


def build_next_edl_from_registered(
    item: RecordingReviewItem | None,
    *,
    start_sec: float,
    end_sec: float,
    cuts: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """登録済み EDL の safe_bounds / boundary_candidates を保ったまま trim と cuts を差し替える。"""
    if item is None or not isinstance(item.edl_json, dict) or not item.edl_json:
        return None
    next_edl = copy.deepcopy(item.edl_json)
    next_edl["schema_version"] = 1
    next_edl["trim"] = {"start_sec": start_sec, "end_sec": end_sec}
    if cuts is not None:
        next_edl["cuts"] = copy.deepcopy(cuts)
    elif "cuts" not in next_edl or not isinstance(next_edl["cuts"], list):
        next_edl["cuts"] = []
    return next_edl


def build_order_log_extra(
    detail_id: int,
    order: RecordingEditOrder,
    next_edl: dict[str, Any] | None,
) -> dict[str, Any]:
    """確定時の構造化ログ用メトリクス（cuts 採否件数・採用尺）を組み立てる。"""
    cuts_list = next_edl.get("cuts", []) if isinstance(next_edl, dict) else []
    cut_metrics = summarize_cuts(cuts_list if isinstance(cuts_list, list) else [])
    adopted_sec = 0.0
    if isinstance(next_edl, dict):
        try:
            adopted_sec = extract_adopted_duration_sec(next_edl)
        except EDLValidationError:
            adopted_sec = 0.0
    return {
        "event_type": "recording_review_trim_order_submitted",
        "event_detail_id": detail_id,
        "order_id": order.pk,
        "base_revision": order.base_revision,
        "target_revision": order.target_revision,
        "adopted_duration_sec": adopted_sec,
        **cut_metrics,
    }
