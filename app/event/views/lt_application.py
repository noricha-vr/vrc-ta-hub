import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import FormView, View

from community.models import Community
from event.forms import LTApplicationForm, LTApplicationReviewForm
from event.models import EventDetail

logger = logging.getLogger(__name__)


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
        return context

    def form_valid(self, form):
        # EventDetailを作成
        event = form.cleaned_data['event']
        event_detail = EventDetail.objects.create(
            event=event,
            detail_type='LT',
            theme=form.cleaned_data['theme'],
            speaker=form.cleaned_data['speaker'],
            duration=form.cleaned_data['duration'],
            start_time=event.start_time,
            status='pending',
            applicant=self.request.user,
            additional_info=form.cleaned_data.get('additional_info', ''),
        )

        # 主催者に通知
        from event.notifications import notify_owners_of_new_application
        notify_owners_of_new_application(event_detail, request=self.request)

        messages.success(
            self.request,
            'LT発表を申請しました。主催者の承認をお待ちください。'
        )
        logger.info(
            f'LT申請作成: Community={self.community.name}, Event={event.date}, '
            f'Theme={event_detail.theme}, User={self.request.user.user_name}'
        )

        return redirect('community:detail', pk=self.community.pk)


class LTApplicationReviewView(LoginRequiredMixin, FormView):
    """LT申請の承認/却下ビュー"""
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

        # 既に処理済みの場合
        if self.event_detail.status != 'pending':
            messages.info(request, 'この申請は既に処理されています。')
            return redirect('event:my_list')

        return super().dispatch(request, *args, **kwargs)

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
            f'LT申請{status_text}: EventDetail ID={self.event_detail.pk}, '
            f'Community={self.community.name}, Reviewer={self.request.user.user_name}'
        )

        return redirect('event:my_list')


class LTApplicationApproveView(LoginRequiredMixin, View):
    """LT申請の承認ビュー（AJAX対応）"""

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
            f'LT申請承認: EventDetail ID={event_detail.pk}, '
            f'Community={community.name}, Reviewer={request.user.user_name}'
        )

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'status': 'approved'})

        messages.success(request, '申請を承認しました。')
        return redirect('event:my_list')


class LTApplicationRejectView(LoginRequiredMixin, View):
    """LT申請の却下ビュー（AJAX対応）"""

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
            f'LT申請却下: EventDetail ID={event_detail.pk}, '
            f'Community={community.name}, Reviewer={request.user.user_name}, '
            f'Reason={rejection_reason}'
        )

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'status': 'rejected'})

        messages.success(request, '申請を却下しました。')
        return redirect('event:my_list')
