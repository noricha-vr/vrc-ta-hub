import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import FormView

from event.forms import GoogleCalendarEventForm
from event.models import Event
from event.services.calendar_registration import register_calendar_events

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
        community = self._get_active_community()
        if not community:
            form.add_error(None, 'コミュニティが見つかりません。')
            return self.form_invalid(form)
        try:
            events = register_calendar_events(community, form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)
        except IntegrityError:
            # atomic を抜けた後で判定し、壊れたトランザクションを再利用しない。
            if Event.objects.filter(
                community=community, date=form.cleaned_data['start_date'],
                start_time=form.cleaned_data['start_time'],
            ).exists():
                logger.warning('重複イベント検出: community_id=%s', community.pk)
                form.add_error(None, '同じ日時にすでにイベントが登録されています。')
            else:
                logger.exception('イベントのDB登録に失敗: community_id=%s', community.pk)
                form.add_error(None, 'イベントの登録に失敗しました。')
            return self.form_invalid(form)
        except Exception:
            logger.exception('イベントの登録に失敗: community_id=%s', community.pk)
            form.add_error(None, 'イベントの登録に失敗しました。')
            return self.form_invalid(form)
        if form.cleaned_data['recurrence_type'] == 'none':
            messages.success(self.request, 'イベントが正常に登録されました')
        else:
            messages.success(self.request, f'開催周期を保存し、{len(events)}件の開催予定を登録しました。以後も自動生成されます。')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['community'] = self._get_active_community()
        return context
