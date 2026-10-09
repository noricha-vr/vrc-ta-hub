from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import HttpResponseForbidden


class AuthenticatedForbiddenMixin(UserPassesTestMixin):
    """認証済みユーザーの権限不足を403レスポンスで返す。

    UserPassesTestMixin の標準挙動はログイン済みユーザーに PermissionDenied を
    投げるため、通常の権限不足がアプリ例外として記録されてしまう。
    """

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            return HttpResponseForbidden(self.get_permission_denied_message())
        return super().handle_no_permission()


class StaffRequiredMixin(LoginRequiredMixin, AuthenticatedForbiddenMixin):
    """運営スタッフ（is_staff または superuser）だけを通す。

    未ログインはログイン画面へ送り、ログイン済みで権限が無い人には 403 を返す。
    """

    def test_func(self):
        user = self.request.user
        return user.is_staff or user.is_superuser
