"""Discord 告知の予約送信: 運営スタッフ用の画面と、Cloud Scheduler から呼ぶ送信エンドポイント。"""
from __future__ import annotations

import copy

from django.contrib import messages
from django.db.models import Case, Count, DateTimeField, F, IntegerField, Q, Value, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.generic import CreateView, ListView

from ta_hub.access_mixins import StaffRequiredMixin
from ta_hub.request_token import is_authorized_request

from .delivery import process_due_messages
from .forms import DiscordScheduledMessageForm
from .models import DiscordScheduledMessage

Status = DiscordScheduledMessage.Status
LIST_PAGINATE_BY = 30

CREATED_MESSAGE = '予約しました。送信日時になると告知チャンネルへ送ります。'
UPDATED_MESSAGE = '予約を更新しました。'
CANCELED_MESSAGE = '予約を取り消しました。'
RESENT_MESSAGE = '予約中に戻しました。1 分ほどで告知チャンネルへ送ります。'
NOT_EDITABLE_ERROR = 'この予約は編集・取り消しできません。送信済み・取り消し済みか、送信処理中です。'
NOT_RESENDABLE_ERROR = '再送できるのは、送信に失敗した予約だけです。'


def _detail_redirect(pk: int):
    return redirect('announcement:discord_detail', pk=pk)


class ScheduledMessageListView(StaffRequiredMixin, ListView):
    template_name = 'announcement/discord/list.html'
    context_object_name = 'scheduled_messages'
    paginate_by = LIST_PAGINATE_BY

    def get_queryset(self):
        # 予約中を送信日時の近い順に上へ。送信済み・失敗・取り消しは、その下に新しい順で並べる
        is_not_scheduled = Case(
            When(status=Status.SCHEDULED, then=Value(0)), default=Value(1), output_field=IntegerField(),
        )
        scheduled_at_while_scheduled = Case(
            When(status=Status.SCHEDULED, then=F('scheduled_at')), output_field=DateTimeField(),
        )
        return DiscordScheduledMessage.objects.select_related('created_by').order_by(
            is_not_scheduled, scheduled_at_while_scheduled.asc(), F('scheduled_at').desc(), '-pk',
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(DiscordScheduledMessage.objects.aggregate(
            scheduled_count=Count('pk', filter=Q(status=Status.SCHEDULED)),
            failed_count=Count('pk', filter=Q(status=Status.FAILED)),
        ))
        return context


class ScheduledMessageCreateView(StaffRequiredMixin, CreateView):
    model = DiscordScheduledMessage
    form_class = DiscordScheduledMessageForm
    template_name = 'announcement/discord/create.html'

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, CREATED_MESSAGE)
        return response

    def get_success_url(self):
        return reverse('announcement:discord_detail', kwargs={'pk': self.object.pk})


class ScheduledMessageDetailView(StaffRequiredMixin, View):
    """本文の全文と状態を表示する。予約中なら同じ画面で編集できる。"""

    template_name = 'announcement/discord/detail.html'

    def get(self, request, pk):
        message = self._get_message(pk)
        form = DiscordScheduledMessageForm(instance=message) if message.is_editable else None
        return self._render(message, form)

    def post(self, request, pk):
        message = self._get_message(pk)
        if not message.is_editable:
            messages.error(request, NOT_EDITABLE_ERROR)
            return _detail_redirect(pk)
        # フォームの検証は入力値をインスタンスへ書き込むので、写しを渡して保存済みの表示と混ぜない
        # （DB から読み直さない）
        form = DiscordScheduledMessageForm(request.POST, instance=copy.copy(message))
        if not form.is_valid():
            return self._render(message, form)
        if _apply_edit(pk, form):
            messages.success(request, UPDATED_MESSAGE)
        else:
            messages.error(request, NOT_EDITABLE_ERROR)
        return _detail_redirect(pk)

    def _get_message(self, pk: int) -> DiscordScheduledMessage:
        return get_object_or_404(DiscordScheduledMessage.objects.select_related('created_by'), pk=pk)

    def _render(self, message: DiscordScheduledMessage, form):
        context = {'message': message, 'form': form}
        return render(self.request, self.template_name, context)


def _apply_edit(pk: int, form: DiscordScheduledMessageForm) -> bool:
    """予約中で送信処理に取られていない時だけ、編集内容を保存する（送信処理と競合しないよう条件付き UPDATE）。"""
    updated = DiscordScheduledMessage.objects.editable().filter(pk=pk).update(
        body=form.cleaned_data['body'],
        scheduled_at=form.cleaned_data['scheduled_at'],
        mention_everyone_confirmed=form.mention_everyone_confirmed,
        attempt_count=0,
        next_attempt_at=None,
        last_error='',
        updated_at=timezone.now(),
    )
    return bool(updated)


class ScheduledMessageCancelView(StaffRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, pk):
        get_object_or_404(DiscordScheduledMessage, pk=pk)
        canceled = DiscordScheduledMessage.objects.editable().filter(pk=pk).update(
            status=Status.CANCELED, next_attempt_at=None, updated_at=timezone.now(),
        )
        if canceled:
            messages.success(request, CANCELED_MESSAGE)
        else:
            messages.error(request, NOT_EDITABLE_ERROR)
        return _detail_redirect(pk)


class ScheduledMessageResendView(StaffRequiredMixin, View):
    """送信に失敗した予約を予約中に戻す。送信日時を過ぎていれば次の送信処理で送る。"""

    http_method_names = ['post']

    def post(self, request, pk):
        get_object_or_404(DiscordScheduledMessage, pk=pk)
        resent = DiscordScheduledMessage.objects.filter(pk=pk, status=Status.FAILED).update(
            status=Status.SCHEDULED,
            attempt_count=0,
            next_attempt_at=None,
            last_error='',
            lease_token='',
            lease_expires_at=None,
            updated_at=timezone.now(),
        )
        if resent:
            messages.success(request, RESENT_MESSAGE)
        else:
            messages.error(request, NOT_RESENDABLE_ERROR)
        return _detail_redirect(pk)


# Cookie ではなく Request-Token ヘッダーで認証するので、CSRF の検査は外す
# （外さないと、Cloud Scheduler の既定の POST が CSRF で 403 になる）
@csrf_exempt
@never_cache
@require_http_methods(['GET', 'POST'])
def send_scheduled_messages(request):
    """Cloud Scheduler から 1 分ごとに呼び、送信日時を過ぎた予約を告知チャンネルへ送る。"""
    if not is_authorized_request(request):
        return HttpResponse('Unauthorized', status=401)
    return JsonResponse(process_due_messages())
