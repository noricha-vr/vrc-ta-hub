"""Misskey API クライアント。テスト環境では明示許可なしの通信を遮断する。"""

import logging
import os
import re

import requests
from django.conf import settings

from twitter.image_download import download_image
from twitter.x_api import PostTweetResult, _failure_result, _summarize_error_body

logger = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 30
MEDIA_UPLOAD_TIMEOUT_SECONDS = 60
MAX_TEXT_LENGTH = 3000
VISIBILITIES = {'public', 'home', 'followers', 'specified'}


def is_misskey_enabled() -> bool:
    """トークン未設定なら DB 操作を含めて Misskey 段を無効にする。"""
    return bool(os.environ.get('MISSKEY_TOKEN', '').strip())


def _should_block_misskey_api_in_tests() -> bool:
    """X クライアント同様、テスト時の呼び出しには明示許可を必須にする。"""
    return (
        getattr(settings, 'TESTING', False)
        and os.environ.get('MISSKEY_API_ALLOW_TEST_CALLS') != '1'
    )


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
        return _failure_result(error_body='Misskey token is not configured')
    if _should_block_misskey_api_in_tests():
        logger.warning('Blocked Misskey API call in test environment')
        return _failure_result(error_body='Blocked in test environment')

    try:
        url = _api_url(endpoint)
    except ValueError as error:
        logger.warning('%s', error)
        return _failure_result(error_body=str(error))

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
        # 認証値が外部サービスの応答に含まれても通知やログへ渡さない。
        body = response.text if response is not None else type(error).__name__
        body = body.replace(token, '[redacted]')
        logger.error(
            'Misskey API failed: endpoint=%s status=%s %s',
            endpoint, status_code, _summarize_error_body(body),
        )
        return _failure_result(status_code=status_code, error_body=body[:1000])


def upload_file(image_url: str) -> PostTweetResult:
    """検証済みの画像を multipart でアップロードし DriveFile を返す。"""
    if not is_misskey_enabled():
        return _failure_result(error_body='Misskey token is not configured')
    if _should_block_misskey_api_in_tests():
        logger.warning('Blocked Misskey API media upload in test environment')
        return _failure_result(error_body='Blocked in test environment')
    try:
        image = download_image(image_url, log=logger)
    except requests.RequestException as error:
        logger.error('Misskey image download failed: %s', type(error).__name__)
        return _failure_result(error_body='Misskey image download failed')
    if image is None:
        return _failure_result(error_body='Invalid Misskey image')
    image_data, content_type = image
    return _post_api(
        'drive/files/create', files={'file': ('image', image_data, content_type)},
    )


def post_note(text: str, image_url: str | None = None) -> PostTweetResult:
    """本文と任意の画像を投稿する。画像失敗時はノートも再試行する。"""
    if not is_misskey_enabled():
        return _failure_result(error_body='Misskey token is not configured')
    if _should_block_misskey_api_in_tests():
        logger.warning('Blocked Misskey API note post in test environment')
        return _failure_result(error_body='Blocked in test environment')
    visibility = os.environ.get('MISSKEY_VISIBILITY', 'public')
    if not text or len(text) > MAX_TEXT_LENGTH or visibility not in VISIBILITIES:
        logger.warning('Invalid Misskey note text or visibility')
        return _failure_result(error_body='Invalid Misskey note text or visibility')

    payload = {'text': text, 'visibility': visibility, 'noExtractMentions': True}
    if image_url:
        uploaded = upload_file(image_url)
        if not uploaded['ok']:
            return uploaded
        payload['fileIds'] = [uploaded['data']['id']]
    return _post_api('notes/create', payload=payload)
