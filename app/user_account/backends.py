"""メールアドレスで認証する Django 認証バックエンド。"""

from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.http import HttpRequest


class EmailBackend(ModelBackend):
    """大文字小文字を区別しないメールアドレス認証を提供する。"""

    def authenticate(
        self,
        request: HttpRequest | None,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """メールアドレスとパスワードが一致する有効ユーザーを返す。"""
        user_model = get_user_model()
        email = kwargs.get(user_model.EMAIL_FIELD, username)
        if not email or password is None:
            return None

        try:
            user = user_model._default_manager.get(email__iexact=email)
        except (user_model.DoesNotExist, user_model.MultipleObjectsReturned):
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
