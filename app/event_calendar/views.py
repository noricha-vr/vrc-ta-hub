from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import UpdateView

from .forms import CalendarEntryForm
from .models import Community, CalendarEntry


class CalendarEntryUpdateView(LoginRequiredMixin, UpdateView):
    """
    VRCイベントカレンダーのエントリー情報を更新するビュー
    
    Attributes:
        model: CalendarEntryモデル
        form_class: カレンダーエントリーのフォームクラス 
        template_name: 使用するテンプレート
        success_url: 更新成功後のリダイレクト先URL
    """
    model = CalendarEntry
    form_class = CalendarEntryForm
    template_name = 'event_calendar/calendar_entry_form.html'
    success_url = reverse_lazy('account:settings')

    def test_func(self):
        """
        ユーザーがカレンダーエントリーを更新する権限があるか確認
        
        Returns:
            bool: カレンダーエントリーのコミュニティのユーザーと一致する場合True
        """
        calendar_entry = self.get_object()
        return self.request.user == calendar_entry.community.custom_user

    def get_object(self, queryset=None):
        """
        更新対象のカレンダーエントリーを取得。存在しない場合は新規作成
        
        Returns:
            CalendarEntry: 取得または作成したカレンダーエントリー
        """
        community = Community.objects.get(pk=self.kwargs['pk'])
        calendar_entry, created = CalendarEntry.objects.get_or_create(
            community=community,
            defaults={
                'event_detail': community.description,
            }
        )
        return calendar_entry

    def form_valid(self, form):
        """
        フォームのバリデーション成功時の処理
        
        Args:
            form: バリデーション済みのフォーム
            
        Returns:
            HttpResponse: 成功メッセージを含むレスポンス
        """
        response = super().form_valid(form)
        messages.success(self.request, 'VRCイベントカレンダー情報が更新されました。')
        return response
