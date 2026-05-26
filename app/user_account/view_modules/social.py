"""SocialAccount 連携の解除に関する view."""

from allauth.socialaccount.models import SocialAccount
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from user_account.forms import SocialAccountDisconnectForm


class SocialAccountDisconnectView(LoginRequiredMixin, View):
    """パスワード確認付きで SocialAccount を解除する。

    allauth の `socialaccount_connections` はパスワード確認を持たないため、
    本ビューで `request.user.check_password()` による確認を強制する。
    過去に誤ったパスワードを設定していた場合の事前救済が目的。
    """

    template_name = 'socialaccount/disconnect_confirm.html'

    def _get_account(self, request, pk):
        return get_object_or_404(SocialAccount, pk=pk, user=request.user)

    def _ensure_usable_password(self, request):
        if request.user.has_usable_password():
            return None
        messages.error(
            request,
            'パスワード未設定のため解除できません。先にパスワードを設定してください。',
        )
        return redirect('account_set_password')

    def get(self, request, pk):
        account = self._get_account(request, pk)
        redirect_response = self._ensure_usable_password(request)
        if redirect_response is not None:
            return redirect_response
        form = SocialAccountDisconnectForm(user=request.user)
        return render(request, self.template_name, {'form': form, 'account': account})

    def post(self, request, pk):
        account = self._get_account(request, pk)
        redirect_response = self._ensure_usable_password(request)
        if redirect_response is not None:
            return redirect_response
        form = SocialAccountDisconnectForm(request.POST, user=request.user)
        if form.is_valid():
            account.delete()
            messages.success(request, 'Discord 連携を解除しました。')
            return redirect('socialaccount_connections')
        return render(request, self.template_name, {'form': form, 'account': account})
