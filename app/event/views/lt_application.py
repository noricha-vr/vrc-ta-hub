import logging
from collections.abc import Iterable
from datetime import datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import FormView, TemplateView, View

from community.models import Community
from event.forms import LTApplicationForm, LTApplicationReviewForm
from event.models import Event, EventDetail

logger = logging.getLogger(__name__)


def _calc_lt_start_time(event_start_time: time, offset_minutes: int) -> time:
    """集会の start_time に offset(分) を加算した time を返す。

    datetime.time は加算非対応のため、datetime.combine で日付を仮置きして演算する。
    23:50 + 30分 → 00:20 のように 24h を跨ぐケースも循環する（Community.end_time と同じ慣例）。
    """
    base = datetime.combine(datetime.today(), event_start_time)
    return (base + timedelta(minutes=offset_minutes)).time()


def _calc_next_lt_start_time(
    event_start_time: time,
    existing_lt_slots: Iterable[tuple[time, int]],
    offset_minutes: int,
) -> time:
    """既存LTの終了後、またはオフセット後の次の開始時刻を返す。"""
    slots = list(existing_lt_slots)
    if not slots:
        return _calc_lt_start_time(event_start_time, offset_minutes)

    event_start_minutes = event_start_time.hour * 60 + event_start_time.minute
    latest_end_minutes = max(
        ((start_time.hour * 60 + start_time.minute - event_start_minutes) % (24 * 60))
        + duration
        for start_time, duration in slots
    )
    return _calc_lt_start_time(event_start_time, latest_end_minutes)


class LTApplicationCreateView(LoginRequiredMixin, FormView):
    """LT発表の申請ビュー"""
    template_name = 'event/lt_application_form.html'
    form_class = LTApplicationForm

    def dispatch(self, request, *args, **kwargs):
        self.community = get_object_or_404(Community, pk=kwargs['community_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['community'] = self.community
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['community'] = self.community
        event_queryset = context['form'].fields['event'].queryset
        offset = self.community.lt_start_offset_minutes or 0
        context['next_start_times'] = {
            event.pk: _calc_next_lt_start_time(
                event.start_time,
                EventDetail.objects.filter(
                    event=event,
                    detail_type='LT',
                    status__in=['pending', 'approved'],
                ).values_list('start_time', 'duration'),
                offset,
            ).strftime('%H:%M')
            for event in event_queryset
        }
        return context

    def form_valid(self, form):
        event = form.cleaned_data['event']
        offset = self.community.lt_start_offset_minutes or 0
        speaker = form.cleaned_data['speaker']
        x_account = form.cleaned_data.get('x_account', '')

        with transaction.atomic():
            event = Event.objects.select_for_update().get(pk=event.pk)
            lt_start = _calc_next_lt_start_time(
                event.start_time,
                EventDetail.objects.filter(
                    event=event,
                    detail_type='LT',
                    status__in=['pending', 'approved'],
                ).values_list('start_time', 'duration'),
                offset,
            )

            # speaker / x_account を user 側にも反映（LT 申込フォームをプロフィール更新の経路として扱う）
            user = self.request.user
            update_fields = []
            if user.display_name != speaker:
                user.display_name = speaker
                update_fields.append('display_name')
            if user.x_account != x_account:
                user.x_account = x_account
                update_fields.append('x_account')
            if update_fields:
                user.save(update_fields=update_fields)

            event_detail = EventDetail.objects.create(
                event=event,
                detail_type='LT',
                theme=form.cleaned_data['theme'],
                speaker=speaker,
                duration=form.cleaned_data['duration'],
                start_time=lt_start,
                status='pending',
                applicant=user,
                additional_info=form.cleaned_data.get('additional_info', ''),
            )

        # 主催者に通知
        from event.notifications import notify_owners_of_new_application
        notify_owners_of_new_application(event_detail, request=self.request)

        logger.info(
            f'発表申請作成: Community={self.community.name}, Event={event.date}, '
            f'Theme={event_detail.theme}, User={self.request.user.user_name}'
        )

        return redirect('event:lt_application_complete', community_pk=self.community.pk)


class LTApplicationCompleteView(LoginRequiredMixin, TemplateView):
    """発表申請完了ページ"""

    template_name = 'event/lt_application_complete.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['community'] = get_object_or_404(
            Community, pk=self.kwargs['community_pk']
        )
        return context


class LTApplicationReviewView(LoginRequiredMixin, FormView):
    """発表申請の承認/却下ビュー"""
    template_name = 'event/lt_application_review.html'
    form_class = LTApplicationReviewForm

    def dispatch(self, request, *args, **kwargs):
        # LoginRequiredMixinのチェックを先に実行
        # 未ログインの場合はログインページにリダイレクト
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        self.event_detail = get_object_or_404(EventDetail, pk=kwargs['pk'])
        self.community = self.event_detail.event.community

        # 権限チェック
        if not self.community.can_edit(request.user):
            messages.error(request, 'この申請を確認する権限がありません。')
            return redirect('community:detail', pk=self.community.pk)

        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        # 処理済み申請の二重 POST を防ぐ（GET は閲覧モードとして許可）
        if self.event_detail.status != 'pending':
            messages.info(request, 'この申請は既に処理されています。')
            return redirect('event:lt_application_review', pk=self.event_detail.pk)
        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event_detail'] = self.event_detail
        context['community'] = self.community
        return context

    def form_valid(self, form):
        action = form.cleaned_data['action']

        if action == 'approve':
            self.event_detail.status = 'approved'
            status_text = '承認'
        else:
            self.event_detail.status = 'rejected'
            self.event_detail.rejection_reason = form.cleaned_data['rejection_reason']
            status_text = '却下'

        self.event_detail.save()

        # 申請者に通知
        from event.notifications import notify_applicant_of_result
        notify_applicant_of_result(self.event_detail, request=self.request)

        messages.success(self.request, f'申請を{status_text}しました。')
        logger.info(
            f'発表申請{status_text}: EventDetail ID={self.event_detail.pk}, '
            f'Community={self.community.name}, Reviewer={self.request.user.user_name}'
        )

        return redirect('event:my_list')


class LTApplicationApproveView(LoginRequiredMixin, View):
    """発表申請の承認ビュー（AJAX対応）"""

    def post(self, request, pk):
        event_detail = get_object_or_404(EventDetail, pk=pk)
        community = event_detail.event.community

        # 権限チェック
        if not community.can_edit(request.user):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': '権限がありません。'}, status=403)
            messages.error(request, 'この申請を承認する権限がありません。')
            return redirect('event:my_list')

        # 既に処理済みの場合
        if event_detail.status != 'pending':
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': 'この申請は既に処理されています。'}, status=400)
            messages.info(request, 'この申請は既に処理されています。')
            return redirect('event:my_list')

        # 承認処理
        event_detail.status = 'approved'
        event_detail.save()

        # 申請者に通知
        from event.notifications import notify_applicant_of_result
        notify_applicant_of_result(event_detail, request=request)

        logger.info(
            f'発表申請承認: EventDetail ID={event_detail.pk}, '
            f'Community={community.name}, Reviewer={request.user.user_name}'
        )

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'status': 'approved'})

        messages.success(request, '申請を承認しました。')
        return redirect('event:my_list')


class LTApplicationRejectView(LoginRequiredMixin, View):
    """発表申請の却下ビュー（AJAX対応）"""

    def post(self, request, pk):
        event_detail = get_object_or_404(EventDetail, pk=pk)
        community = event_detail.event.community

        # 権限チェック
        if not community.can_edit(request.user):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': '権限がありません。'}, status=403)
            messages.error(request, 'この申請を却下する権限がありません。')
            return redirect('event:my_list')

        # 既に処理済みの場合
        if event_detail.status != 'pending':
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': 'この申請は既に処理されています。'}, status=400)
            messages.info(request, 'この申請は既に処理されています。')
            return redirect('event:my_list')

        # 却下理由を取得
        rejection_reason = request.POST.get('rejection_reason', '').strip()
        if not rejection_reason:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': '却下理由を入力してください。'}, status=400)
            messages.error(request, '却下理由を入力してください。')
            return redirect('event:my_list')

        # 却下処理
        event_detail.status = 'rejected'
        event_detail.rejection_reason = rejection_reason
        event_detail.save()

        # 申請者に通知
        from event.notifications import notify_applicant_of_result
        notify_applicant_of_result(event_detail, request=request)

        logger.info(
            f'発表申請却下: EventDetail ID={event_detail.pk}, '
            f'Community={community.name}, Reviewer={request.user.user_name}, '
            f'Reason={rejection_reason}'
        )

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'status': 'rejected'})

        messages.success(request, '申請を却下しました。')
        return redirect('event:my_list')
