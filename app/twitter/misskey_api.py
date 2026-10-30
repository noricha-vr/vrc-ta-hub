"""Misskey API クライアント。テスト環境では明示許可なしの通信を遮断する。"""

import logging
import os
import re
import socket
import sys

import requests
from django.conf import settings

from twitter.image_download import download_image
from twitter.x_api import PostTweetResult, _failure_result

logger = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 30
MEDIA_UPLOAD_TIMEOUT_SECONDS = 60
MAX_TEXT_LENGTH = 3000
VISIBILITIES = {'public', 'home', 'followers', 'specified'}
# 任意文字列を error.code に含める応答も転送しない。既知のコードだけを許可する。
SAFE_ERROR_CODES = frozenset({
    'ACCESS_DENIED', 'AUTHENTICATION_FAILED', 'CREDENTIAL_REQUIRED',
    'INTERNAL_ERROR', 'INVALID_PARAM', 'INVALID_TOKEN', 'NO_SUCH_FILE',
    'PERMISSION_DENIED', 'RATE_LIMIT_EXCEEDED',
})
PRE_SEND_CONNECTION_ERROR_TYPES = frozenset({
    'ConnectTimeoutError', 'NameResolutionError', 'NewConnectionError',
})


def is_misskey_enabled() -> bool:
    """トークン未設定なら DB 操作を含めて Misskey 段を無効にする。"""
    return bool(os.environ.get('MISSKEY_TOKEN', '').strip())


def _should_block_misskey_api_in_tests() -> bool:
    """設定または実行ランナーでテストを判定し、明示許可を必須にする。"""
    testing = getattr(settings, 'TESTING', False) or any(
        arg == 'test' or re.fullmatch(r'(?:pytest|py\.test)(?:[-.].*)?', os.path.basename(arg))
        for arg in sys.argv
    ) or (
        'pytest' in sys.modules
        or any(
            module_name in sys.modules
            for module_name in ('unittest.__main__', 'unittest.main', 'unittest.runner')
        )
    )
    return (
        testing
        and os.environ.get('MISSKEY_API_ALLOW_TEST_CALLS') != '1'
    )


def _safe_error_code(response) -> str:
    """応答本文を保持せず、許可した JSON error.code だけを取り出す。"""
    if response is not None:
        try:
            data = response.json()
        except ValueError:
            data = None
        error = data.get('error') if isinstance(data, dict) else None
        code = error.get('code') if isinstance(error, dict) else None
        if isinstance(code, str) and code in SAFE_ERROR_CODES:
            return code
    return 'UNKNOWN_ERROR'


def _misskey_failure_result(
    *,
    status_code: int | None = None,
    error_body: str | None = None,
    retryable: bool = False,
    outcome_unknown: bool = False,
) -> PostTweetResult:
    """Misskey の再送可否・結果不明を、X API の戻り値へ追加する。"""
    return {
        **_failure_result(status_code=status_code, error_body=error_body),
        'retryable': retryable,
        'outcome_unknown': outcome_unknown,
    }


def _is_pre_send_connection_failure(error: BaseException) -> bool:
    """接続確立前に終わったと確認できる requests 接続エラーだけを返す。"""
    if isinstance(error, requests.ConnectTimeout):
        return True
    if not isinstance(error, requests.ConnectionError):
        return False

    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, socket.gaierror) or type(current).__name__ in PRE_SEND_CONNECTION_ERROR_TYPES:
            return True
        for attribute in ('reason', '__cause__', '__context__'):
            nested = getattr(current, attribute, None)
            if isinstance(nested, BaseException):
                pending.append(nested)
        pending.extend(arg for arg in getattr(current, 'args', ()) if isinstance(arg, BaseException))
    return False


def _api_url(endpoint: str) -> str:
    """環境変数はホスト名として扱い、パスや認証情報の混入を拒否する。"""
    host = os.environ.get('MISSKEY_HOST', 'misskey.io')
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]{1,5})?', host):
        raise ValueError('MISSKEY_HOST must be a hostname')
    return f'https://{host}/api/{endpoint}'


def _post_api(endpoint: str, *, payload=None, files=None) -> PostTweetResult:
    """JSON または multipart を送り、ID を持つ成功応答だけを受理する。"""
    token = os.environ.get('MISSKEY_TOKEN', '').strip()
    if not token:
        return _misskey_failure_result(error_body='Misskey token is not configured')
    if _should_block_misskey_api_in_tests():
        logger.warning('Blocked Misskey API call in test environment')
        return _misskey_failure_result(error_body='Blocked in test environment', retryable=True)

    try:
        url = _api_url(endpoint)
    except ValueError as error:
        logger.warning('%s', error)
        return _misskey_failure_result(error_body=str(error))

    request_args = {
        'timeout': MEDIA_UPLOAD_TIMEOUT_SECONDS if files else REQUEST_TIMEOUT_SECONDS,
        'allow_redirects': False,
    }
    if files:
        request_args.update(data={'i': token}, files=files)
    else:
        request_args['json'] = {'i': token, **(payload or {})}

    response = None
    try:
        response = requests.post(url, **request_args)
        response.raise_for_status()
        if not 200 <= response.status_code < 300:
            raise requests.HTTPError(response=response)
        data = response.json()
        if endpoint == 'notes/create' and isinstance(data, dict):
            data = data.get('createdNote')
        if not isinstance(data, dict) or not isinstance(data.get('id'), str) or not data['id']:
            raise ValueError('Missing id in Misskey response')
        return {'ok': True, 'data': data, 'status_code': response.status_code, 'error_body': None}
    except (requests.RequestException, ValueError) as error:
        error_response = getattr(error, 'response', None)
        if error_response is not None:
            response = error_response
        status_code = response.status_code if response is not None else None
        code = _safe_error_code(response)
        has_http_failure = status_code is not None and 400 <= status_code < 600
        unknown_note_result = (
            endpoint == 'notes/create'
            and (
                (response is not None and status_code is not None and 200 <= status_code < 300)
                or (response is None and not _is_pre_send_connection_failure(error))
            )
        )
        retryable = has_http_failure or (
            response is None and _is_pre_send_connection_failure(error)
        )
        if endpoint == 'drive/files/create' and not has_http_failure:
            # アップロードの結果不明では孤立ファイルだけが残る。告知は未送信なので再試行できる。
            retryable = True
        logger.error(
            'Misskey API failed: endpoint=%s status=%s code=%s',
            endpoint, status_code, code,
        )
        return _misskey_failure_result(
            status_code=status_code,
            error_body=code,
            retryable=retryable,
            outcome_unknown=unknown_note_result,
        )


def upload_file(image_url: str) -> PostTweetResult:
    """検証済みの画像を multipart でアップロードし DriveFile を返す。"""
    if not is_misskey_enabled():
        return _misskey_failure_result(error_body='Misskey token is not configured')
    if _should_block_misskey_api_in_tests():
        logger.warning('Blocked Misskey API media upload in test environment')
        return _misskey_failure_result(error_body='Blocked in test environment', retryable=True)
    try:
        image = download_image(image_url, log=logger)
    except requests.RequestException as error:
        logger.error('Misskey image download failed: %s', type(error).__name__)
        return _misskey_failure_result(error_body='Misskey image download failed', retryable=True)
    if image is None:
        return _misskey_failure_result(error_body='Invalid Misskey image', retryable=True)
    image_data, content_type = image
    return _post_api(
        'drive/files/create', files={'file': ('image', image_data, content_type)},
    )


def post_note(text: str, image_url: str | None = None) -> PostTweetResult:
    """本文と任意の画像を投稿する。画像失敗時はノートも再試行する。"""
    if not is_misskey_enabled():
        return _misskey_failure_result(error_body='Misskey token is not configured')
    if _should_block_misskey_api_in_tests():
        logger.warning('Blocked Misskey API note post in test environment')
        return _misskey_failure_result(error_body='Blocked in test environment', retryable=True)
    visibility = os.environ.get('MISSKEY_VISIBILITY', 'public')
    if not text or len(text) > MAX_TEXT_LENGTH or visibility not in VISIBILITIES:
        logger.warning('Invalid Misskey note text or visibility')
        return _misskey_failure_result(error_body='Invalid Misskey note text or visibility')

    payload = {'text': text, 'visibility': visibility, 'noExtractMentions': True}
    if image_url:
        uploaded = upload_file(image_url)
        if not uploaded['ok']:
            return uploaded
        payload['fileIds'] = [uploaded['data']['id']]
    return _post_api('notes/create', payload=payload)
