from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect
from django.views import View

from community.models import Community

import community.views as community_views


class UpdateWebhookView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Webhook URL更新ビュー"""

    def test_func(self):
        community = get_object_or_404(Community, pk=self.kwargs['pk'])
        return community.can_edit(self.request.user)

    def post(self, request, pk):
        community = get_object_or_404(Community, pk=pk)
        webhook_url = request.POST.get('notification_webhook_url', '').strip()

        if webhook_url and not webhook_url.startswith('https://discord.com/api/webhooks/'):
            messages.error(request, 'Discord Webhook URLの形式が正しくありません。')
            return redirect('community:settings')

        community.notification_webhook_url = webhook_url if webhook_url else ''
        community.save(update_fields=['notification_webhook_url'])

        if webhook_url:
            messages.success(request, 'Discord Webhook URLを保存しました。')
        else:
            messages.success(request, 'Discord Webhook URLをクリアしました。')

        return redirect('community:settings')


class TestWebhookView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Webhookテスト送信ビュー"""

    def test_func(self):
        community = get_object_or_404(Community, pk=self.kwargs['pk'])
        return community.can_edit(self.request.user)

    def post(self, request, pk):
        community = get_object_or_404(Community, pk=pk)

        if not community.notification_webhook_url:
            messages.error(request, 'Webhook URLが設定されていません。')
            return redirect('community:settings')

        test_message = {
            "content": f"**【テスト通知】** {community.name}\n"
            "このメッセージはテスト送信です。Webhook設定が正しく動作しています。"
        }

        webhook_timeout_seconds = 10
        try:
            response = community_views.requests.post(
                community.notification_webhook_url,
                json=test_message,
                timeout=webhook_timeout_seconds,
            )
            if response.status_code == 204:
                messages.success(request, 'テスト通知を送信しました。Discordを確認してください。')
            else:
                messages.error(
                    request,
                    f'通知の送信に失敗しました。(ステータスコード: {response.status_code})',
                )
        except community_views.requests.Timeout:
            messages.error(request, '通知の送信がタイムアウトしました。')
        except community_views.requests.RequestException as exc:
            community_views.logger.error(f'Webhook送信エラー: {exc}')
            messages.error(request, '通知の送信中にエラーが発生しました。')

        return redirect('community:settings')


class LTApplicationListView(LoginRequiredMixin, View):
    """LT申請一覧ビュー（マイリストへリダイレクト）"""

    def get(self, request, pk):
        messages.info(request, 'LT申請一覧はマイリストに統合されました。')
        return redirect('event:my_list')


class UpdateLTSettingsView(LoginRequiredMixin, UserPassesTestMixin, View):
    """LT申請設定更新ビュー"""

    def test_func(self):
        community = get_object_or_404(Community, pk=self.kwargs['pk'])
        return community.can_edit(self.request.user)

    def post(self, request, pk):
        community = get_object_or_404(Community, pk=pk)
        accepts_lt = request.POST.get('accepts_lt_application') == 'on'
        lt_template = request.POST.get('lt_application_template', '').strip()
        duration_str = request.POST.get('default_lt_duration', '30').strip()

        try:
            duration = max(1, int(duration_str))
        except ValueError:
            duration = 30

        community.accepts_lt_application = accepts_lt
        community.lt_application_template = lt_template
        community.default_lt_duration = duration
        community.save(
            update_fields=[
                'accepts_lt_application',
                'lt_application_template',
                'default_lt_duration',
            ]
        )

        messages.success(request, 'LT申請設定を保存しました。')
        community_views.logger.info(
            f'LT申請設定更新: 集会「{community.name}」、'
            f'テンプレート文字数={len(lt_template)}、デフォルト発表時間={duration}分'
        )

        return redirect('community:settings')
