"""録画運用ダッシュボード向けスナップショット受信 API のシリアライザと検証。"""
from __future__ import annotations

import copy
import json
import math
import re
from typing import Any

from django.utils.dateparse import parse_datetime
from rest_framework import serializers

from event.models import RecordingOpsSnapshot

from .recording_ops_serializers import StrictIntegerField, StrictJSONDictField

SUPPORTED_SNAPSHOT_SCHEMA_VERSION = 1
MAX_SNAPSHOT_PAYLOAD_BYTES = 256 * 1024
MAX_SNAPSHOT_NESTING_DEPTH = 32
REQUIRED_SNAPSHOT_SECTIONS = (
    "next_session",
    "action_items",
    "funnel_stats",
    "venue_coverage",
    "upcoming_schedule",
)
# 公開リポジトリ・DB へ VRChat 生 ID や接続 URI を保存させないための多重防御パターン。
FORBIDDEN_SNAPSHOT_PATTERN = re.compile(
    r"(?:wrld_|usr_|grp_|inst_|vrchat://)",
    re.IGNORECASE,
)


class AwareISO8601DateTimeField(serializers.DateTimeField):
    """タイムゾーン付きの ISO8601 日時文字列のみ受け付ける厳格な日時フィールド。"""

    def to_internal_value(self, value: Any):
        if not isinstance(value, str):
            self.fail("invalid", format="ISO8601")
        try:
            parsed = parse_datetime(value)
        except ValueError as exc:
            raise serializers.ValidationError("タイムゾーン付きの ISO8601 日時文字列を指定してください。") from exc
        if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
            raise serializers.ValidationError("タイムゾーン付きの ISO8601 日時文字列を指定してください。")
        return parsed


class StrictJSONListField(serializers.JSONField):
    """JSON 配列（list）のみ受け付ける JSON フィールド。"""

    def to_internal_value(self, data: Any):
        value = super().to_internal_value(data)
        if not isinstance(value, list):
            self.fail("invalid")
        return value


class NullableStrictJSONDictField(serializers.JSONField):
    """JSON オブジェクト（dict）または null のみ受け付ける JSON フィールド。"""

    def __init__(self, **kwargs):
        kwargs.setdefault("allow_null", True)
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any):
        if data is None:
            return None
        value = super().to_internal_value(data)
        if not isinstance(value, dict):
            self.fail("invalid")
        return value


def _validate_snapshot_tree(value: Any, *, path: str = "$", depth: int = 0) -> None:
    """JSON 構造内のネスト深度・非有限浮動小数点数・禁止パターンを再帰的に検査する。"""
    if depth > MAX_SNAPSHOT_NESTING_DEPTH:
        raise serializers.ValidationError(
            f"スナップショットのネスト深度が上限（{MAX_SNAPSHOT_NESTING_DEPTH}）を超えています。"
        )
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise serializers.ValidationError(f"{path} のキーは文字列である必要があります。")
            if FORBIDDEN_SNAPSHOT_PATTERN.search(key):
                raise serializers.ValidationError("スナップショットに許可されていない識別子パターンが含まれています。")
            _validate_snapshot_tree(item, path=f"{path}.{key}", depth=depth + 1)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_snapshot_tree(item, path=f"{path}[{index}]", depth=depth + 1)
        return
    if isinstance(value, str):
        if FORBIDDEN_SNAPSHOT_PATTERN.search(value):
            raise serializers.ValidationError("スナップショットに許可されていない識別子パターンが含まれています。")
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise serializers.ValidationError("スナップショットに非有限の数値（NaN / Infinity）は指定できません。")


class RecordingOpsSnapshotIngestSerializer(serializers.Serializer):
    """統計スナップショット受信リクエストの契約検証と保存を行う。"""

    schema_version = StrictIntegerField(
        required=True,
        min_value=SUPPORTED_SNAPSHOT_SCHEMA_VERSION,
        max_value=SUPPORTED_SNAPSHOT_SCHEMA_VERSION,
    )
    snapshot_at = AwareISO8601DateTimeField(required=True)
    next_session = NullableStrictJSONDictField(required=True)
    action_items = StrictJSONListField(required=True)
    funnel_stats = StrictJSONDictField(required=True)
    venue_coverage = StrictJSONDictField(required=True)
    upcoming_schedule = StrictJSONListField(required=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        raw_payload = self.initial_data
        if not isinstance(raw_payload, dict):
            raise serializers.ValidationError({"detail": "JSON オブジェクトを指定してください。"})

        try:
            _validate_snapshot_tree(raw_payload)
            encoded = json.dumps(
                raw_payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise serializers.ValidationError({"detail": "シリアライズ可能な JSON オブジェクトを指定してください。"}) from exc

        if len(encoded) > MAX_SNAPSHOT_PAYLOAD_BYTES:
            raise serializers.ValidationError(
                {"detail": f"スナップショットのサイズが上限（{MAX_SNAPSHOT_PAYLOAD_BYTES} バイト）を超えています。"}
            )

        # 後方互換のためトップレベルおよび各セクション内の未知キーをそのまま保持する。
        attrs["_raw_payload"] = copy.deepcopy(raw_payload)
        attrs["_payload_bytes"] = len(encoded)
        return attrs

    def create(self, validated_data: dict[str, Any]) -> RecordingOpsSnapshot:
        return RecordingOpsSnapshot.objects.create(
            schema_version=validated_data["schema_version"],
            snapshot_at=validated_data["snapshot_at"],
            payload=validated_data["_raw_payload"],
        )


class RecordingOpsSnapshotResponseSerializer(serializers.ModelSerializer):
    """統計スナップショット登録 API のレスポンスシリアライザ。"""

    class Meta:
        model = RecordingOpsSnapshot
        fields = [
            "id",
            "schema_version",
            "snapshot_at",
            "received_at",
        ]
        read_only_fields = fields
