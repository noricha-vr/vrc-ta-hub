"""APIキー管理に関する view 群."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.views import View
from django.views.generic import ListView

from user_account.models import APIKey


class APIKeyListView(LoginRequiredMixin, ListView):
    model = APIKey
    template_name = 'account/api_key_list.html'
    context_object_name = 'api_keys'

    def get_queryset(self):
        return self.request.user.api_keys.filter(is_active=True).order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['new_api_key'] = self.request.session.pop('new_api_key', None)
        context['new_api_key_name'] = self.request.session.pop('new_api_key_name', None)
        return context


class APIKeyCreateView(LoginRequiredMixin, View):
    def post(self, request):
        name = request.POST.get('name', '')
        api_key, raw_key = APIKey.create_with_raw_key(
            user=request.user,
            name=name,
        )
        request.session['new_api_key'] = raw_key
        request.session['new_api_key_name'] = api_key.name or "APIキー"
        messages.success(
            request,
            f'APIキー「{api_key.name or "APIキー"}」を作成しました。'
            'キーは一度だけ表示されます。必ず安全な場所に保管してください。',
        )
        return redirect('account:api_key_list')


class APIKeyDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        try:
            api_key = APIKey.objects.get(pk=pk, user=request.user)
            api_key.is_active = False
            api_key.save()
            messages.success(request, f'APIキー「{api_key.name or "APIキー"}」を無効化しました。')
        except APIKey.DoesNotExist:
            messages.error(request, 'APIキーが見つかりません。')
        return redirect('account:api_key_list')
