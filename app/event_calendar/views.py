from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import UpdateView
from django.core.cache import cache

from .forms import CalendarEntryForm
from .models import Community, CalendarEntry
from .calendar_utils import create_calendar_entry_url


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
        # pkなしでログインユーザーに紐付くコミュニティを取得
        community = Community.objects.get(custom_user=self.request.user)
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
        
        # カレンダーエントリーに関連するイベントのキャッシュを削除
        community = self.object.community
        for event in community.events.all():
            cache_key = f'calendar_entry_url_{event.id}'
            cache.delete(cache_key)
            
        messages.success(self.request, 'VRCイベントカレンダー情報が更新されました。')
        return response

    def generate_google_calendar_url(self, event):
        """Googleカレンダーにイベントを追加するためのURLを生成する"""
        from urllib.parse import quote
        from django.urls import reverse

        # イベントの開始と終了の日時を設定
        start_datetime = datetime.combine(event.date, event.start_time)
        end_datetime = start_datetime + timedelta(minutes=event.duration)

        # タイムゾーンを設定
        start_datetime = timezone.localtime(
            timezone.make_aware(start_datetime))
        end_datetime = timezone.localtime(timezone.make_aware(end_datetime))

        # コミュニティページのURLを生成
        community_url = self.request.build_absolute_uri(
            reverse('community:detail', kwargs={'pk': event.community.pk})
        )

        # 説明文を作成
        description = [f"参加方法: {community_url}"]

        # 発表情報を追加（存在する場合）
        if event.details.exists():
            description.extend([f"発表者: {detail.speaker}\nテーマ: {
            detail.theme}" for detail in event.details.all()])

        # URLパラメータを作成
        params = {
            'action': 'TEMPLATE',
            'text': f"{event.community.name}",  # イベントのタイトル
            'dates': f"{start_datetime.strftime('%Y%m%dT%H%M%S')}/{end_datetime.strftime('%Y%m%dT%H%M%S')}",
            'ctz': 'Asia/Tokyo',  # タイムゾーン
            'details': "\n\n".join(description)  # 説明文
        }

        # URLを構築
        base_url = "https://www.google.com/calendar/render?"
        param_strings = [f"{k}={quote(str(v))}" for k, v in params.items()]

        return base_url + "&".join(param_strings)
