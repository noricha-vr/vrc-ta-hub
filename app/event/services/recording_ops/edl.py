"""EDL v1 スキーマ検証サービス（Layer 1）。"""
from __future__ import annotations

import copy
import math
from decimal import Decimal
from typing import Any

EDL_SCHEMA_VERSION = 1
EDL_CUT_ACTIONS = frozenset({"pending", "accept", "reject"})
# 途中カット（action="accept"）確定の有効化フラグ（緊急抑止時は False に戻す）。
ALLOW_ACCEPT_CUTS: bool = True
MIN_KEEP_DURATION_SEC = Decimal("1.0")
MAX_CUTS_COUNT = 200
_TOP_LEVEL_REQUIRED_KEYS = frozenset({"schema_version", "safe_bounds", "trim", "cuts"})
_TOP_LEVEL_ALLOWED_KEYS = _TOP_LEVEL_REQUIRED_KEYS | {"boundary_candidates"}
_BOUNDARY_CANDIDATE_KEYS = frozenset({"start", "end"})
_CUT_REQUIRED_KEYS = frozenset({"id", "start_sec", "end_sec", "kind", "label", "action"})
_CUT_ALLOWED_KEYS = _CUT_REQUIRED_KEYS | {"source", "confidence"}


class EDLValidationError(ValueError):
    """EDL v1 スキーマの検証に失敗した。"""


def _require_finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EDLValidationError(f"{field_name} は数値で指定してください。")
    try:
        numeric = float(value)
    except OverflowError as exc:
        raise EDLValidationError(f"{field_name} が表現可能な数値範囲を超えています。") from exc
    if not math.isfinite(numeric):
        raise EDLValidationError(f"{field_name} に NaN や無限大は指定できません。")
    return numeric


def _ensure_no_nonfinite_numbers(value: Any, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise EDLValidationError(f"{path} に NaN や無限大は指定できません。")
    if isinstance(value, dict):
        for key, item in value.items():
            _ensure_no_nonfinite_numbers(item, f"{path}.{key}")
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            _ensure_no_nonfinite_numbers(item, f"{path}[{idx}]")


def _validate_boundary_candidates(value: Any) -> None:
    if not isinstance(value, dict):
        raise EDLValidationError("boundary_candidates はオブジェクトで指定してください。")
    unknown_keys = set(value.keys()) - _BOUNDARY_CANDIDATE_KEYS
    if unknown_keys:
        raise EDLValidationError(f"boundary_candidates に未対応のキーがあります: {', '.join(sorted(unknown_keys))}")
    for side in ("start", "end"):
        if side not in value:
            continue
        candidates = value[side]
        if not isinstance(candidates, list):
            raise EDLValidationError(f"boundary_candidates.{side} は配列で指定してください。")
        for idx, item in enumerate(candidates):
            item_path = f"boundary_candidates.{side}[{idx}]"
            if isinstance(item, dict):
                if "sec" in item:
                    sec = _require_finite_number(item["sec"], f"{item_path}.sec")
                    if sec < 0:
                        raise EDLValidationError(f"{item_path}.sec は 0 以上で指定してください。")
                if "time_sec" in item:
                    sec = _require_finite_number(item["time_sec"], f"{item_path}.time_sec")
                    if sec < 0:
                        raise EDLValidationError(f"{item_path}.time_sec は 0 以上で指定してください。")
            elif not isinstance(item, bool) and isinstance(item, (int, float)):
                sec = _require_finite_number(item, item_path)
                if sec < 0:
                    raise EDLValidationError(f"{item_path} は 0 以上で指定してください。")
            else:
                raise EDLValidationError(f"{item_path} は数値またはオブジェクトで指定してください。")


def _validate_cuts(
    cuts: Any,
    *,
    trim_start: float,
    trim_end: float,
) -> Decimal:
    if not isinstance(cuts, list):
        raise EDLValidationError("cuts は配列で指定してください。")
    if len(cuts) > MAX_CUTS_COUNT:
        raise EDLValidationError(f"cuts は {MAX_CUTS_COUNT} 件以下で指定してください。")

    seen_ids: set[str] = set()
    prev_accepted_end: float | None = None
    accepted_duration = Decimal("0")

    for idx, cut in enumerate(cuts):
        prefix = f"cuts[{idx}]"
        if not isinstance(cut, dict):
            raise EDLValidationError(f"{prefix} はオブジェクトで指定してください。")
        missing_keys = _CUT_REQUIRED_KEYS - set(cut.keys())
        if missing_keys:
            raise EDLValidationError(f"{prefix} に必須キーがありません: {', '.join(sorted(missing_keys))}")
        unknown_keys = set(cut.keys()) - _CUT_ALLOWED_KEYS
        if unknown_keys:
            raise EDLValidationError(f"{prefix} に未対応のキーがあります: {', '.join(sorted(unknown_keys))}")

        cut_id = cut["id"]
        if not isinstance(cut_id, str) or not cut_id.strip():
            raise EDLValidationError(f"{prefix}.id は空でない文字列で指定してください。")
        if cut_id in seen_ids:
            raise EDLValidationError(f"{prefix}.id が重複しています: {cut_id}")
        seen_ids.add(cut_id)

        cut_start = _require_finite_number(cut["start_sec"], f"{prefix}.start_sec")
        cut_end = _require_finite_number(cut["end_sec"], f"{prefix}.end_sec")
        if not (trim_start <= cut_start < cut_end <= trim_end):
            raise EDLValidationError(
                f"{prefix} の区間は trim.start_sec <= start_sec < end_sec <= trim.end_sec を満たす必要があります。"
            )

        kind = cut["kind"]
        if not isinstance(kind, str) or not kind.strip():
            raise EDLValidationError(f"{prefix}.kind は空でない文字列で指定してください。")
        label = cut["label"]
        if not isinstance(label, str):
            raise EDLValidationError(f"{prefix}.label は文字列で指定してください。")

        if "source" in cut and cut["source"] is not None and not isinstance(cut["source"], str):
            raise EDLValidationError(f"{prefix}.source は文字列で指定してください。")
        if "confidence" in cut and cut["confidence"] is not None:
            confidence = _require_finite_number(cut["confidence"], f"{prefix}.confidence")
            if not (0.0 <= confidence <= 1.0):
                raise EDLValidationError(f"{prefix}.confidence は 0.0 以上 1.0 以下で指定してください。")

        action = cut["action"]
        if not isinstance(action, str) or action not in EDL_CUT_ACTIONS:
            raise EDLValidationError(f"{prefix}.action は pending / accept / reject のいずれかで指定してください。")

        if action == "accept":
            if prev_accepted_end is not None and cut_start <= prev_accepted_end:
                raise EDLValidationError(
                    f"{prefix} の accept 区間が前の accept 区間と重複・接触または順序逆転しています。"
                )
            prev_accepted_end = cut_end
            accepted_duration += Decimal(str(cut_end)) - Decimal(str(cut_start))

    return accepted_duration


def validate_edl_v1(edl_json: Any) -> dict[str, Any]:
    """EDL v1 ペイロードを検証し、正規化済みのディープコピーを返す。"""
    if not isinstance(edl_json, dict):
        raise EDLValidationError("edl_json は JSON オブジェクトで指定してください。")

    _ensure_no_nonfinite_numbers(edl_json, "edl_json")

    missing_top = _TOP_LEVEL_REQUIRED_KEYS - set(edl_json.keys())
    if missing_top:
        raise EDLValidationError(f"edl_json に必須キーがありません: {', '.join(sorted(missing_top))}")
    unknown_top = set(edl_json.keys()) - _TOP_LEVEL_ALLOWED_KEYS
    if unknown_top:
        raise EDLValidationError(f"edl_json に未対応のキーがあります: {', '.join(sorted(unknown_top))}")

    schema_version = edl_json["schema_version"]
    if type(schema_version) is not int or schema_version != EDL_SCHEMA_VERSION:
        raise EDLValidationError("schema_version は整数 1 のみ対応しています。")

    safe_bounds = edl_json["safe_bounds"]
    if not isinstance(safe_bounds, dict) or set(safe_bounds.keys()) != {"min_sec", "max_sec"}:
        raise EDLValidationError("safe_bounds は min_sec と max_sec を持つオブジェクトで指定してください。")
    safe_min = _require_finite_number(safe_bounds["min_sec"], "safe_bounds.min_sec")
    safe_max = _require_finite_number(safe_bounds["max_sec"], "safe_bounds.max_sec")
    if not (0.0 <= safe_min < safe_max):
        raise EDLValidationError("safe_bounds は 0 <= min_sec < max_sec を満たす必要があります。")

    trim = edl_json["trim"]
    if not isinstance(trim, dict) or set(trim.keys()) != {"start_sec", "end_sec"}:
        raise EDLValidationError("trim は start_sec と end_sec を持つオブジェクトで指定してください。")
    trim_start = _require_finite_number(trim["start_sec"], "trim.start_sec")
    trim_end = _require_finite_number(trim["end_sec"], "trim.end_sec")
    if not (safe_min <= trim_start < trim_end <= safe_max):
        raise EDLValidationError(
            "trim は safe_bounds.min_sec <= start_sec < end_sec <= safe_bounds.max_sec を満たす必要があります。"
        )

    if "boundary_candidates" in edl_json:
        _validate_boundary_candidates(edl_json["boundary_candidates"])

    accepted_duration = _validate_cuts(
        edl_json["cuts"],
        trim_start=trim_start,
        trim_end=trim_end,
    )
    trim_duration = Decimal(str(trim_end)) - Decimal(str(trim_start))
    if trim_duration - accepted_duration < MIN_KEEP_DURATION_SEC:
        raise EDLValidationError(
            f"trim から accept カットを差し引いた維持時間（keep_spans 合計）は {MIN_KEEP_DURATION_SEC} 秒以上である必要があります。"
        )

    return copy.deepcopy(edl_json)


def compute_keep_spans(edl_json: dict[str, Any]) -> list[tuple[float, float]]:
    """検証済み EDL v1 から accept カットを除いた維持区間（keep_spans）一覧を返す。"""
    validated = validate_edl_v1(edl_json)
    trim_start = float(validated["trim"]["start_sec"])
    trim_end = float(validated["trim"]["end_sec"])
    spans: list[tuple[float, float]] = []
    cursor = trim_start
    for cut in validated["cuts"]:
        if cut["action"] != "accept":
            continue
        cut_start = float(cut["start_sec"])
        cut_end = float(cut["end_sec"])
        if cut_start > cursor:
            spans.append((cursor, cut_start))
        cursor = max(cursor, cut_end)
    if cursor < trim_end:
        spans.append((cursor, trim_end))
    return spans


def summarize_cuts(cuts: list[dict[str, Any]]) -> dict[str, int]:
    """cuts 配列の件数・採否内訳・手動/自動件数を集計する。"""
    summary = {
        "cuts_total": len(cuts),
        "cuts_accept_count": 0,
        "cuts_reject_count": 0,
        "cuts_pending_count": 0,
        "cuts_manual_count": 0,
        "cuts_auto_accept_count": 0,
        "cuts_auto_reject_count": 0,
    }
    for cut in cuts:
        action = cut.get("action")
        is_manual = cut.get("source") == "staff"
        if is_manual:
            summary["cuts_manual_count"] += 1
        if action == "accept":
            summary["cuts_accept_count"] += 1
            if not is_manual:
                summary["cuts_auto_accept_count"] += 1
        elif action == "reject":
            summary["cuts_reject_count"] += 1
            if not is_manual:
                summary["cuts_auto_reject_count"] += 1
        elif action == "pending":
            summary["cuts_pending_count"] += 1
    return summary


def extract_adopted_duration_sec(edl_json: dict[str, Any]) -> float:
    """検証済み EDL v1 から採用尺（秒）を返す。"""
    validated = validate_edl_v1(edl_json)
    trim = validated["trim"]
    trim_duration = Decimal(str(float(trim["end_sec"]))) - Decimal(str(float(trim["start_sec"])))
    accepted_duration = sum(
        (
            Decimal(str(float(cut["end_sec"]))) - Decimal(str(float(cut["start_sec"])))
            for cut in validated["cuts"]
            if cut["action"] == "accept"
        ),
        Decimal("0"),
    )
    return float(trim_duration - accepted_duration)
