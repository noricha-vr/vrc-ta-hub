import logging

from urllib.parse import urlencode

from django.conf import settings
from django.utils.cache import add_never_cache_headers, patch_vary_headers
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import FormView, TemplateView, View

from community.models import Community
from event.forms import LTApplicationForm, LTApplicationReviewForm
from event.models import Event, EventDetail
from event.services.application_review import ApplicationAlreadyReviewed, review_application
from event.services.lt_schedule import _calc_next_lt_start_time
from vket.application_link import application_warnings
from ta_hub.og import accepts_public_application, application_metadata

logger = logging.getLogger(__name__)


class LTApplicationCreateView(LoginRequiredMixin, FormView):
    """LT発表の申請ビュー"""
    template_name = 'event/lt_application_form.html'
    form_class = LTApplicationForm

    def dispatch(self, request, *args, **kwargs):
        self.community = get_object_or_404(Community, pk=kwargs['community_pk'])
        if (request.method == 'GET' and not request.user.is_authenticated
                and accepts_public_application(self.community)):
            metadata = application_metadata(request, self.community)
            response = render(request, 'ta_hub/page_public.html', {
                **metadata,
                'public_title': metadata['og_title'],
                'public_message': 'ログインして発表をお申し込みください。',
                'login_label': 'ログインして発表を申し込む',
                'login_url': f'{settings.LOGIN_URL}?{urlencode({"next": request.get_full_path()})}',
            })
            add_never_cache_headers(response)
            patch_vary_headers(response, ['Cookie'])
            response['X-Robots-Tag'] = 'noindex'
            return response
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['community'] = self.community
        kwargs['user'] = self.request.user
        kwargs['fixed_event_pk'] = self.kwargs.get('event_pk')
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['community'] = self.community
        context.update(application_metadata(self.request, self.community))
        event_queryset = context['form'].fields['event'].queryset
        lt_prefetch = Prefetch(
            'details',
            queryset=EventDetail.objects.filter(
                detail_type='LT',
                status__in=['pending', 'approved'],
            ),
            to_attr='lt_slots',
        )
        offset = self.community.lt_start_offset_minutes or 0
        context['next_start_times'] = {
            event.pk: _calc_next_lt_start_time(
                event.start_time,
                ((slot.start_time, slot.duration) for slot in event.lt_slots),
                offset,
            ).strftime('%H:%M')
            for event in event_queryset.prefetch_related(lt_prefetch)
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
                duration=self.community.default_lt_duration,
                start_time=lt_start,
                status='pending',
                applicant=user,
                additional_info=form.cleaned_data.get('additional_info', ''),
                recording_policy=form.resolved_recording_policy(),
                article_consent=form.cleaned_data['article_consent'],
            )

        for warning in application_warnings(event_detail):
            messages.warning(self.request, warning)

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

        self.event_detail = get_object_or_404(EventDetail.objects.select_related('event__community'), pk=kwargs['pk'])
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

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['event_detail'] = self.event_detail
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event_detail'] = self.event_detail
        context['community'] = self.community
        context.update(application_metadata(self.request, self.community))
        return context

    def form_valid(self, form):
        action = form.cleaned_data['action']
        try:
            result = review_application(
                self.event_detail,
                'approved' if action == 'approve' else 'rejected',
                reviewer=self.request.user,
                request=self.request,
                rejection_reason=form.cleaned_data['rejection_reason'],
                event=form.cleaned_data.get('event') if action == 'approve' else None,
                start_time=form.cleaned_data.get('start_time'),
                duration=form.cleaned_data.get('duration'),
            )
        except ApplicationAlreadyReviewed:
            messages.info(self.request, 'この申請は既に処理されています。')
            return redirect('event:lt_application_review', pk=self.event_detail.pk)

        self.event_detail = result.event_detail
        schedule_changes = result.schedule_changes
        status_text = '承認' if action == 'approve' else '却下'
        for warning in result.warnings:
            messages.warning(self.request, warning)

        messages.success(self.request, f'申請を{status_text}しました。')
        logger.info(
            f'発表申請{status_text}: EventDetail ID={self.event_detail.pk}, '
            f'Community={self.community.name}, Reviewer={self.request.user.user_name}'
        )
        if schedule_changes:
            logger.info(
                '発表申請の日時変更: EventDetail ID=%s, changes=%s',
                self.event_detail.pk,
                ', '.join(
                    f"{c['label']}: {c['before']} → {c['after']}" for c in schedule_changes
                ),
            )

        return redirect('event:my_list')


class LTApplicationApproveView(LoginRequiredMixin, View):
    """発表申請の承認ビュー（AJAX対応）"""

    def post(self, request, pk):
        event_detail = get_object_or_404(EventDetail.objects.select_related('event__community'), pk=pk)
        community = event_detail.event.community

        # 権限チェック
        if not community.can_edit(request.user):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': '権限がありません。'}, status=403)
            messages.error(request, 'この申請を承認する権限がありません。')
            return redirect('event:my_list')

        try:
            result = review_application(event_detail, 'approved', reviewer=request.user, request=request)
        except ApplicationAlreadyReviewed:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': 'この申請は既に処理されています。'}, status=400)
            messages.info(request, 'この申請は既に処理されています。')
            return redirect('event:my_list')

        event_detail = result.event_detail
        warnings = result.warnings
        for warning in warnings:
            messages.warning(request, warning)

        logger.info(
            f'発表申請承認: EventDetail ID={event_detail.pk}, '
            f'Community={community.name}, Reviewer={request.user.user_name}'
        )

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'status': 'approved', **({'warnings': warnings} if warnings else {})})

        messages.success(request, '申請を承認しました。')
        return redirect('event:my_list')


class LTApplicationRejectView(LoginRequiredMixin, View):
    """発表申請の却下ビュー（AJAX対応）"""

    def post(self, request, pk):
        event_detail = get_object_or_404(EventDetail.objects.select_related('event__community'), pk=pk)
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

        try:
            result = review_application(
                event_detail, 'rejected', reviewer=request.user, request=request,
                rejection_reason=rejection_reason,
            )
        except ApplicationAlreadyReviewed:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': 'この申請は既に処理されています。'}, status=400)
            messages.info(request, 'この申請は既に処理されています。')
            return redirect('event:my_list')
        event_detail = result.event_detail

        logger.info(
            f'発表申請却下: EventDetail ID={event_detail.pk}, '
            f'Community={community.name}, Reviewer={request.user.user_name}, '
            f'Reason={rejection_reason}'
        )

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'status': 'rejected'})

        messages.success(request, '申請を却下しました。')
        return redirect('event:my_list')
