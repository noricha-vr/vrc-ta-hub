from django.utils import timezone
from rest_framework import authentication
from rest_framework import exceptions

from user_account.models import APIKey


class APIKeyAuthentication(authentication.BaseAuthentication):
    """APIキー認証クラス"""
    
    def authenticate(self, request):
        # ヘッダーからAPIキーを取得
        raw_api_key = self.get_api_key(request)
        
        if not raw_api_key:
            return None

        api_key_hash = APIKey.hash_raw_key(raw_api_key)
            
        try:
            # APIキーを検証
            key_obj = APIKey.objects.select_related('user').get(
                key=api_key_hash,
                is_active=True
            )
        except APIKey.DoesNotExist:
            raise exceptions.AuthenticationFailed('無効なAPIキーです。')

        # 有効期限切れ・IPホワイトリスト不一致・無効ユーザーはすべて
        # 「無効なAPIキーです。」に統一する（情報漏洩防止）
        if not key_obj.is_valid(request):
            raise exceptions.AuthenticationFailed('無効なAPIキーです。')
        if not key_obj.user.is_active:
            raise exceptions.AuthenticationFailed('無効なAPIキーです。')

        # すべての検証通過後に last_used を更新（失敗キーの観測を残さない）
        key_obj.last_used = timezone.now()
        key_obj.save(update_fields=['last_used'])

        return (key_obj.user, key_obj)
    
    def get_api_key(self, request):
        """リクエストからAPIキーを取得"""
        # Authorizationヘッダーから取得
        auth_header = request.META.get('HTTP_AUTHORIZATION')
        if auth_header and auth_header.startswith('Bearer '):
            return auth_header.split(' ')[1]
        
        # X-API-Keyヘッダーから取得
        return request.META.get('HTTP_X_API_KEY')
