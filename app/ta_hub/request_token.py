"""Cloud Scheduler などから呼ぶエンドポイントの Request-Token 認証。"""
from __future__ import annotations

from django.conf import settings
from django.utils.crypto import constant_time_compare

REQUEST_TOKEN_HEADER = 'Request-Token'


def is_authorized_request(request) -> bool:
    """Request-Token ヘッダーが settings.REQUEST_TOKEN と一致するかを定数時間で比べる。

    サーバー側のトークンが未設定（空）の時は、ヘッダーに関係なく常に拒否する（fail-closed）。
    設定漏れで認証なしのエンドポイントになるのを防ぐため。
    """
    expected = getattr(settings, 'REQUEST_TOKEN', '') or ''
    if not expected:
        return False
    provided = request.headers.get(REQUEST_TOKEN_HEADER, '')
    return constant_time_compare(provided, expected)
