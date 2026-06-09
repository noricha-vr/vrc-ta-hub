"""api_v1 request body validation schemas (Pydantic)

既存 DRF Serializer は output (to_representation) でそのまま使い、
input validation のみ Pydantic に統一する。

エラーメッセージは既存 API レスポンス互換のため、日本語固定文字列で返す。
View 側は ValidationError をキャッチして既存の {"success": False, "error": "..."}
フォーマットに変換する。

このモジュールが ``schemas.py`` ではなく ``input_schemas.py`` という名前なのは、
``api_v1/schemas.py`` が drf-spectacular の OpenAPI スキーマ拡張用に既に使われており、
責務（OpenAPI スキーマ拡張 vs. request body validation）を混在させないため。
"""
from __future__ import annotations

from datetime import date as _date, datetime, time as _time
from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# View 側でエラー種別を判別するためのメッセージ定数。
# 既存テストが response.data["error"] の部分文字列マッチで判定しているため、
# Pydantic の ValidationError から元メッセージを取り出す経路でも同じ文言を維持する。
ERROR_BASE_DATE_REQUIRED = "基準日が指定されていません"
ERROR_CUSTOM_RULE_REQUIRED = "カスタムルールが指定されていません"
ERROR_DATE_FORMAT_PREFIX = "日付形式が正しくありません"


class RecurrencePreviewInput(BaseModel):
    """RecurrencePreviewAPIView.post の request body schema.

    既存実装と完全互換にするため、空文字 / 未指定 / 型違いに対する
    エラー文言は ERROR_* 定数で固定する。
    """

    # extra="ignore" は付ける（既存クライアントが追加フィールドを送る可能性があり、
    # 後方互換性を優先する。要件: "既存 API 応答形式を壊さない"）。
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=False)

    frequency: str = Field(default="WEEKLY", description="WEEKLY/MONTHLY_BY_DATE/MONTHLY_BY_WEEK/OTHER")
    custom_rule: str = ""
    # base_date / base_time は文字列受けして field_validator で parse する。
    # 理由: Pydantic 標準の date 型は YYYY-MM-DD 以外も一部許容してしまうが、
    # 既存テストは "2026/01/01" を 400 で弾く挙動を要求しているため、
    # strptime ベースで厳密に検証する。
    base_date: Optional[_date] = None
    base_time: _time = _time(22, 0)
    interval: int = Field(default=1, ge=1, le=12)
    week_of_month: Optional[int] = Field(default=None, ge=1, le=5)
    weekday: Optional[int] = Field(default=None, ge=0, le=6)
    months: int = Field(default=3, ge=1, le=12)
    community_id: Optional[int] = None

    @field_validator("base_date", mode="before")
    @classmethod
    def _parse_base_date(cls, v: Union[str, _date, None]) -> Optional[_date]:
        """base_date を YYYY-MM-DD 文字列から date に厳密 parse する."""
        if v is None or v == "":
            return None
        if isinstance(v, _date):
            return v
        if isinstance(v, str):
            try:
                return datetime.strptime(v, "%Y-%m-%d").date()
            except ValueError as exc:
                # メッセージは既存 except ValueError ブロックと同じ prefix で返す。
                raise ValueError(f"{ERROR_DATE_FORMAT_PREFIX}: {exc}") from exc
        raise ValueError(f"{ERROR_DATE_FORMAT_PREFIX}: base_date は文字列で指定してください")

    @field_validator("base_time", mode="before")
    @classmethod
    def _parse_base_time(cls, v: Union[str, _time, None]) -> _time:
        """base_time を HH:MM 文字列から time に厳密 parse する."""
        if v is None or v == "":
            return _time(22, 0)
        if isinstance(v, _time):
            return v
        if isinstance(v, str):
            try:
                return datetime.strptime(v, "%H:%M").time()
            except ValueError as exc:
                raise ValueError(f"{ERROR_DATE_FORMAT_PREFIX}: {exc}") from exc
        raise ValueError(f"{ERROR_DATE_FORMAT_PREFIX}: base_time は文字列で指定してください")

    @model_validator(mode="after")
    def _check_required_combinations(self) -> "RecurrencePreviewInput":
        """既存実装の必須チェックを再現する.

        Pydantic 標準の Required は base_date 未指定で 422 相当エラーになるが、
        既存 API では「基準日が指定されていません」固定文言を返すため、
        Optional + model_validator で明示メッセージに揃える。
        """
        if self.base_date is None:
            raise ValueError(ERROR_BASE_DATE_REQUIRED)
        if self.frequency == "OTHER" and not self.custom_rule:
            raise ValueError(ERROR_CUSTOM_RULE_REQUIRED)
        return self
