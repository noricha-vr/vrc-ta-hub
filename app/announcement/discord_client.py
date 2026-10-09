"""告知チャンネルの webhook へメッセージを 1 回だけ送る。

再試行は予約の側（delivery）が間を空けて行う。共通の website.discord_webhook はその場で
最大 3 回送り直すため、Discord に届いたのに応答だけ失われた時に同じ告知が二重に届く。そのため使わない。

送り直してよい（retryable）のは、本文が Discord に届いていないと言える失敗だけにする。
- HTTP 429（Discord が受け付けずに断った）
- 接続を張る前の失敗（接続のタイムアウト・接続の拒否・名前解決・TLS のハンドシェイク）
5xx・応答待ちのタイムアウト・送った後の切断は、届いたかどうか分からない。自動で送り直すと
二重に届くおそれがあるため送り直さず、チャンネルを確認してからの再送（人の判断）に任せる。

webhook の URL はトークンを含む秘密の値なので、戻り値・ログ・例外の文字列に含めない。
requests の例外の文字列には URL が入るため、例外は型の名前だけを使う。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import requests
from django.conf import settings
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError

from website.discord_webhook import is_discord_webhook_url

CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 10
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR_MIN = 500
# Discord のエラー応答から画面に残す説明の最大長
ERROR_DETAIL_MAX_LENGTH = 200
URL_IN_TEXT_PATTERN = re.compile(r'https?://\S+')

# 届いたかどうか分からない失敗に添える案内
MAY_HAVE_ARRIVED_GUIDE = '届いている可能性があるため、チャンネルを確認してから再送してください。'
WEBHOOK_NOT_CONFIGURED_ERROR = '送信先の webhook（DISCORD_ANNOUNCE_WEBHOOK_URL）が設定されていません。'
WEBHOOK_INVALID_ERROR = '送信先の webhook の設定が Discord の webhook の形式ではありません。'


@dataclass(frozen=True)
class SendResult:
    """1 回の送信の結果。retryable は「届いていないと言えるので、時間をおいて送り直してよい」。"""

    ok: bool
    message_id: str = ''
    error: str = ''
    retryable: bool = False


def build_allowed_mentions(*, mention_everyone: bool) -> dict:
    """ロールとユーザーのメンションは通し、@everyone / @here は確認済みの時だけ通す。"""
    parse = ['roles', 'users']
    if mention_everyone:
        parse.append('everyone')
    return {'parse': parse}


def send_announcement(content: str, *, mention_everyone: bool) -> SendResult:
    """告知チャンネルへ 1 回だけ送り、Discord の応答で結果を判定する。"""
    webhook_url = getattr(settings, 'DISCORD_ANNOUNCE_WEBHOOK_URL', '') or ''
    if not webhook_url:
        return SendResult(ok=False, error=WEBHOOK_NOT_CONFIGURED_ERROR)
    # 設定の取り違えで別の宛先へ送らないよう、集会の通知先と同じ検証（discord.com のみ）を通す
    if not is_discord_webhook_url(webhook_url):
        return SendResult(ok=False, error=WEBHOOK_INVALID_ERROR)

    payload = {
        'content': content,
        'allowed_mentions': build_allowed_mentions(mention_everyone=mention_everyone),
    }
    try:
        # wait=true で、Discord が作ったメッセージ（ID を含む）を応答で受け取る
        response = requests.post(
            webhook_url,
            params={'wait': 'true'},
            json=payload,
            timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
        )
    except requests.RequestException as error:
        return _result_from_exception(error)
    return _result_from_response(response)


def _result_from_exception(error: requests.RequestException) -> SendResult:
    error_type = type(error).__name__
    if _failed_before_sending(error):
        return SendResult(
            ok=False, error=f'Discord に接続できませんでした（{error_type}）。', retryable=True,
        )
    # 送った後の失敗（応答待ちのタイムアウト・切断など）は、届いたかどうか分からない
    return SendResult(
        ok=False, error=f'Discord の応答を確認できませんでした（{error_type}）。{MAY_HAVE_ARRIVED_GUIDE}',
    )


def _failed_before_sending(error: requests.RequestException) -> bool:
    """接続を張る前に失敗したか（本文が Discord に届いていないと言えるか）。"""
    # TLS のハンドシェイクの失敗（SSLError）も、本文を送る前に止まっている
    if isinstance(error, (requests.ConnectTimeout, requests.exceptions.SSLError)):
        return True
    if not isinstance(error, requests.ConnectionError) or not error.args:
        return False
    reason = getattr(error.args[0], 'reason', None)
    return isinstance(reason, (NewConnectionError, ConnectTimeoutError))


def _result_from_response(response: requests.Response) -> SendResult:
    status_code = response.status_code
    if 200 <= status_code < 300:
        return SendResult(ok=True, message_id=_message_id(response))
    error = f'Discord が HTTP {status_code} を返しました{_error_detail(response)}。'
    if status_code == HTTP_TOO_MANY_REQUESTS:
        # 混雑で断られた（受け付けていない）ので、時間をおいて送り直してよい
        return SendResult(ok=False, error=error, retryable=True)
    if status_code >= HTTP_SERVER_ERROR_MIN:
        # 5xx は Discord 側で作られたかどうか分からないので、送り直さない
        return SendResult(ok=False, error=f'{error}{MAY_HAVE_ARRIVED_GUIDE}')
    return SendResult(ok=False, error=error)


def _json_object(response: requests.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _message_id(response: requests.Response) -> str:
    message_id = _json_object(response).get('id')
    return str(message_id) if message_id else ''


def _error_detail(response: requests.Response) -> str:
    """Discord のエラー応答の message と code だけを、URL を伏せて短くして返す。"""
    data = _json_object(response)
    parts = [str(data[key]) for key in ('message', 'code') if data.get(key) not in (None, '')]
    if not parts:
        return ''
    detail = URL_IN_TEXT_PATTERN.sub('[URL]', ' / '.join(parts))[:ERROR_DETAIL_MAX_LENGTH]
    return f'（{detail}）'
