"""API v1 共通の DRF 例外ハンドラ。

DRF 標準のハンドラはレスポンス body に ``detail`` しか載せず、``ErrorDetail.code``
（機械可読なエラー種別）はクライアントへ届かない。ここで ``code`` を付与し、
API v1 のエラー契約を ``{"detail": "...", "code": "..."}`` に統一する。

既存クライアント互換のため **既存フィールドは削除・改名しない**（追加のみ）。
ValidationError のフィールド別エラー辞書もキーはそのまま維持し、
``detail`` / ``code`` を衝突しない場合だけ追記する。
"""

from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler as drf_exception_handler

# 例外から code を決定できなかった場合の既定値。
DEFAULT_ERROR_CODE = "error"
DEFAULT_ERROR_DETAIL = "エラーが発生しました。"
# 入力検証失敗の top-level code。DRF 既定の "invalid" ではなくこの値に統一し、
# 手書きで返している API v1 の検証エラー（recurrence_preview / views）と揃える。
VALIDATION_ERROR_CODE = "validation_error"


def _resolve_code(exc) -> str:
    """例外から機械可読な code 文字列を取り出す。

    DRF の ``APIException.get_codes()`` は detail の構造に応じて
    str / list / dict を返すため、レスポンス直下に載せられる単一の文字列へ畳み込む。
    """
    if isinstance(exc, ValidationError):
        # フィールド別 code は各フィールド側に残る。top-level はエンドポイント横断で統一する。
        return VALIDATION_ERROR_CODE

    codes = exc.get_codes() if hasattr(exc, "get_codes") else None

    if isinstance(codes, str):
        return codes
    if isinstance(codes, list):
        for item in codes:
            if isinstance(item, str):
                return item

    return str(getattr(exc, "default_code", DEFAULT_ERROR_CODE))


def _first_message(value) -> str | None:
    """ネストした DRF エラー構造から最初のメッセージ文字列を取り出す。"""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for item in value.values():
            message = _first_message(item)
            if message is not None:
                return message
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            message = _first_message(item)
            if message is not None:
                return message
    return None


def _resolve_detail(exc, data=None) -> str:
    """レスポンス直下に載せる detail を決める。

    フィールド別エラー（``{"start_time": ["開始時刻は..."]}``）では、既に日本語の
    メッセージが入っているのでその先頭を採用する。例外クラスの ``default_detail`` は
    英語（"Invalid input." 等）なので最後の手段にしない。
    """
    message = _first_message(data)
    if message:
        return message
    if isinstance(exc, ValidationError):
        return DEFAULT_ERROR_DETAIL
    return str(getattr(exc, "default_detail", DEFAULT_ERROR_DETAIL))


def api_exception_handler(exc, context):
    """DRF 標準ハンドラの結果に ``code`` / ``detail`` を追記する。"""
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    code = _resolve_code(exc)
    data = response.data

    if isinstance(data, dict):
        # detail は元のエラー内容（フィールド別メッセージ）から決めるため、
        # code を差し込む前に確定させる。
        detail = None if "detail" in data else _resolve_detail(exc, data)
        if "code" not in data:
            data["code"] = code
        if detail is not None:
            data["detail"] = detail
    elif isinstance(data, list):
        # 非フィールドエラーがリストで返るケース。元の配列は errors として保持する。
        detail = _resolve_detail(exc, data)
        response.data = {"detail": detail, "code": code, "errors": data}

    return response
