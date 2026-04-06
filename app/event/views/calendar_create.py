import logging
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import FormView

from event.forms import GoogleCalendarEventForm
from event.models import Event

logger = logging.getLogger(__name__)


class GoogleCalendarEventCreateView(LoginRequiredMixin, FormView):
    template_name = 'event/calendar_form.html'
    form_class = GoogleCalendarEventForm
    success_url = reverse_lazy('event:my_list')

    def _get_active_community(self):
        """アクティブな集会を取得する"""
        active_community_id = self.request.session.get('active_community_id')
        if active_community_id:
            membership = self.request.user.community_memberships.filter(
                community_id=active_community_id
            ).select_related('community').first()
            if membership:
                return membership.community

        # フォールバック: 最初の管理集会
        membership = self.request.user.community_memberships.select_related('community').first()
        if membership:
            return membership.community

        return None

    def dispatch(self, request, *args, **kwargs):
        # LoginRequiredMixin の認証チェックを先に実行
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        # コミュニティの承認状態をチェック
        community = self._get_active_community()
        if not community or community.status != 'approved':
            messages.error(request, '集会が承認されていないため、カレンダーにイベントを登録できません。')
            return redirect('event:my_list')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # ログインユーザーのコミュニティを初期値として設定
        if self.request.user.is_authenticated:
            community = self._get_active_community()
            if community:
                kwargs['initial'] = {
                    'start_time': community.start_time,
                    'duration': community.duration
                }
        return kwargs

    def form_valid(self, form):
        try:
            # フォームのバリデーション後にコミュニティを取得
            community = self._get_active_community()
            if not community:
                messages.error(self.request, 'コミュニティが見つかりません')
                return self.form_invalid(form)

            start_date = form.cleaned_data['start_date']
            start_time = form.cleaned_data['start_time']

            logger.info(f'イベント登録開始: コミュニティ={community.name}, 日付={start_date}, 開始時間={start_time}')

            # 同じ日時のイベントが存在するかチェック
            existing_event = Event.objects.filter(
                date=start_date,
                start_time=start_time,
                community=community
            ).first()

            if existing_event:
                logger.warning(
                    f'重複イベント検出: ID={existing_event.id}, コミュニティ={community.name}, 日付={start_date}, 開始時間={start_time}')
                messages.error(self.request, f'同じ日時（{start_date} {start_time}）にすでにイベントが登録されています。')
                return self.form_invalid(form)

            # 開始時刻と終了時刻を設定
            start_datetime = datetime.combine(start_date, start_time)
            duration = form.cleaned_data['duration']

            # 新しいイベントをDBに保存
            try:
                new_event = Event.objects.create(
                    community=community,
                    date=start_date,
                    start_time=start_time,
                    duration=duration,
                    weekday=start_datetime.strftime("%a")
                    # google_calendar_event_idは同期時に設定される
                )
                logger.info(f'イベントをDBに登録: ID={new_event.id}, 日付={start_date}, 開始時間={start_time}')

                # イベントの作成が成功した場合、キャッシュをクリア
                cache_key = f'calendar_entry_url_{new_event.id}'
                cache.delete(cache_key)

                messages.success(self.request, 'イベントが正常に登録されました')

            except Exception as e:
                logger.error(f'イベントのDB登録でエラー: {str(e)}', exc_info=True)
                messages.error(self.request, 'イベントの登録に失敗しました')
                return self.form_invalid(form)

            return super().form_valid(form)

        except Exception:
            logger.exception("イベントの登録に失敗しました")
            messages.error(self.request, 'イベントの登録に失敗しました')
            return self.form_invalid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['community'] = self._get_active_community()
        return context
