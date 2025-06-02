from django.utils import timezone
from rest_framework import authentication
from rest_framework import exceptions

from account.models import APIKey


class APIKeyAuthentication(authentication.BaseAuthentication):
    """APIキー認証クラス"""
    
    def authenticate(self, request):
        # ヘッダーからAPIキーを取得
        api_key = self.get_api_key(request)
        
        if not api_key:
            return None
            
        try:
            # APIキーを検証
            key_obj = APIKey.objects.select_related('user').get(
                key=api_key,
                is_active=True
            )
            
            # 最終使用時刻を更新
            key_obj.last_used = timezone.now()
            key_obj.save(update_fields=['last_used'])
            
            # ユーザーが有効か確認
            if not key_obj.user.is_active:
                raise exceptions.AuthenticationFailed('ユーザーが無効です。')
                
            return (key_obj.user, key_obj)
            
        except APIKey.DoesNotExist:
            raise exceptions.AuthenticationFailed('無効なAPIキーです。')
    
    def get_api_key(self, request):
        """リクエストからAPIキーを取得"""
        # Authorizationヘッダーから取得
        auth_header = request.META.get('HTTP_AUTHORIZATION')
        if auth_header and auth_header.startswith('Bearer '):
            return auth_header.split(' ')[1]
        
        # X-API-Keyヘッダーから取得
        return request.META.get('HTTP_X_API_KEY')