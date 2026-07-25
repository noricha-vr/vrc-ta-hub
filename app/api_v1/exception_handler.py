"""API v1 共通の DRF 例外ハンドラ。

DRF 標準のハンドラはレスポンス body に ``detail`` しか載せず、``ErrorDetail.code``
（機械可読なエラー種別）はクライアントへ届かない。ここで ``code`` を付与し、
API v1 のエラー契約を ``{"detail": "...", "code": "..."}`` に統一する。

既存クライアント互換のため **既存フィールドは削除・改名しない**（追加のみ）。
ValidationError のフィールド別エラー辞書もキーはそのまま維持し、
``detail`` / ``code`` を衝突しない場合だけ追記する。
"""

from rest_framework.views import exception_handler as drf_exception_handler

# 例外から code を決定できなかった場合の既定値。
DEFAULT_ERROR_CODE = "error"
DEFAULT_ERROR_DETAIL = "エラーが発生しました。"


def _resolve_code(exc) -> str:
    """例外から機械可読な code 文字列を取り出す。

    DRF の ``APIException.get_codes()`` は detail の構造に応じて
    str / list / dict を返すため、レスポンス直下に載せられる単一の文字列へ畳み込む。
    """
    codes = exc.get_codes() if hasattr(exc, "get_codes") else None

    if isinstance(codes, str):
        return codes
    if isinstance(codes, list):
        for item in codes:
            if isinstance(item, str):
                return item
    if isinstance(codes, dict):
        # フィールド別 ValidationError。個々のフィールド code はフィールド側に残るため、
        # レスポンス直下は代表値 (default_code) にする。
        return str(getattr(exc, "default_code", DEFAULT_ERROR_CODE))

    return str(getattr(exc, "default_code", DEFAULT_ERROR_CODE))


def _resolve_detail(exc) -> str:
    return str(getattr(exc, "default_detail", DEFAULT_ERROR_DETAIL))


def api_exception_handler(exc, context):
    """DRF 標準ハンドラの結果に ``code`` / ``detail`` を追記する。"""
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    code = _resolve_code(exc)
    data = response.data

    if isinstance(data, dict):
        if "code" not in data:
            data["code"] = code
        if "detail" not in data:
            data["detail"] = _resolve_detail(exc)
    elif isinstance(data, list):
        # 非フィールドエラーがリストで返るケース。元の配列は errors として保持する。
        detail = str(data[0]) if data else _resolve_detail(exc)
        response.data = {"detail": detail, "code": code, "errors": data}

    return response
