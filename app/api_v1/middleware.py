import logging

from django.utils.deprecation import MiddlewareMixin

from user_account.models import APIKey

from .models import APIRequestLog

logger = logging.getLogger(__name__)

_API_PREFIX = '/api/v1/'
_PATH_MAX = 500
_UA_MAX = 500
_IP_MAX = 64


def _client_ip(request) -> str:
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()[:_IP_MAX]
    return (request.META.get('REMOTE_ADDR') or '')[:_IP_MAX]


def _resolve_auth(request):
    """認証状態を (auth_method, user, api_key) で返す。

    APIKey 認証は DRF レイヤーの authentication.py が
    request.auth に APIKey オブジェクトを詰めるため、そこから拾う。
    """
    user = getattr(request, 'user', None)
    if user is None or not getattr(user, 'is_authenticated', False):
        return APIRequestLog.AUTH_ANONYMOUS, None, None

    auth = getattr(request, 'auth', None)
    if isinstance(auth, APIKey):
        return APIRequestLog.AUTH_API_KEY, user, auth
    return APIRequestLog.AUTH_SESSION, user, None


class APIRequestLogMiddleware(MiddlewareMixin):
    """/api/v1/* のリクエストを APIRequestLog テーブルに記録する。

    レスポンス処理の最後に 1 レコード INSERT する。ログ書き込みで
    本体レスポンスを壊さないよう全例外を握りつぶし、logger に警告のみ残す。
    """

    def process_response(self, request, response):
        try:
            path = request.path or ''
            if not path.startswith(_API_PREFIX):
                return response

            auth_method, user, api_key = _resolve_auth(request)
            APIRequestLog.objects.create(
                user=user,
                api_key=api_key,
                auth_method=auth_method,
                path=path[:_PATH_MAX],
                method=(request.method or '')[:8],
                status_code=response.status_code,
                remote_ip=_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:_UA_MAX],
            )
        except Exception:
            logger.warning('APIRequestLog 保存に失敗', exc_info=True)
        return response
