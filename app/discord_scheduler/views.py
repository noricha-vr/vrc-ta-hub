"""運営向けの予約画面と、認証付きの定期送信エンドポイント。"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import FormView, TemplateView

from ta_hub.access_mixins import AuthenticatedForbiddenMixin

from .forms import ScheduledDiscordPostForm
from .models import ScheduledDiscordPost
from .services import is_configured, process_scheduled_posts


@method_decorator(never_cache, name='dispatch')
class StaffRequiredMixin(LoginRequiredMixin, AuthenticatedForbiddenMixin):
    def test_func(self):
        user = self.request.user
        return user.is_active and (user.is_staff or user.is_superuser)


def _destination_context():
    ready = is_configured()
    return {
        'destination_ready': ready,
        'channel_url': settings.DISCORD_SCHEDULED_CHANNEL_URL.strip() if ready else '',
    }


class PostListView(StaffRequiredMixin, TemplateView):
    template_name = 'discord_scheduler/post_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pending = [ScheduledDiscordPost.Status.SCHEDULED, ScheduledDiscordPost.Status.SENDING]
        context.update(_destination_context())
        context['pending_posts'] = ScheduledDiscordPost.objects.filter(
            status__in=pending,
        ).order_by('scheduled_at', 'pk')
        history = ScheduledDiscordPost.objects.exclude(status__in=pending).order_by('-updated_at', '-pk')
        context['history_page'] = Paginator(history, 20).get_page(self.request.GET.get('page'))
        return context


class PostCreateView(StaffRequiredMixin, FormView):
    template_name = 'discord_scheduler/post_form.html'
    form_class = ScheduledDiscordPostForm
    success_url = reverse_lazy('discord_scheduler:post_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_destination_context())
        context['is_edit'] = False
        return context

    def form_valid(self, form):
        if not is_configured():
            form.add_error(None, '投稿先の設定が必要です。設定が完了してから予約してください。')
            return self.form_invalid(form)
        post = form.save(commit=False)
        post.created_by = self.request.user
        post.save()
        messages.success(self.request, 'Discordへの投稿を予約しました。')
        return super().form_valid(form)


class PostEditView(StaffRequiredMixin, View):
    def _render(self, request, form, post):
        return render(request, 'discord_scheduler/post_form.html', {
            **_destination_context(), 'form': form, 'post': post, 'is_edit': True,
        })

    def get(self, request, pk):
        post = get_object_or_404(ScheduledDiscordPost, pk=pk)
        if post.status != ScheduledDiscordPost.Status.SCHEDULED:
            messages.error(request, '送信開始後または取消済みの予約は変更できません。')
            return redirect('discord_scheduler:post_list')
        return self._render(request, ScheduledDiscordPostForm(instance=post), post)

    def post(self, request, pk):
        post = get_object_or_404(ScheduledDiscordPost, pk=pk)
        form = ScheduledDiscordPostForm(request.POST, instance=post)
        if not is_configured():
            form.is_valid()
            form.add_error(None, '投稿先の設定が必要です。設定が完了してから更新してください。')
            return self._render(request, form, post)
        if form.is_valid():
            # 画面表示後に送信処理が開始していても、本文や日時を変更しない。
            updated = ScheduledDiscordPost.objects.filter(
                pk=post.pk, status=ScheduledDiscordPost.Status.SCHEDULED,
            ).update(
                content=form.cleaned_data['content'],
                scheduled_at=form.cleaned_data['scheduled_at'],
                updated_at=timezone.now(),
            )
            if updated:
                messages.success(request, '予約を更新しました。')
            else:
                messages.error(request, '送信が開始されたか、予約が取り消されたため変更できませんでした。')
            return redirect('discord_scheduler:post_list')
        return self._render(request, form, post)


class PostCancelView(StaffRequiredMixin, View):
    def get(self, request, pk):
        post = get_object_or_404(ScheduledDiscordPost, pk=pk)
        if post.status != ScheduledDiscordPost.Status.SCHEDULED:
            messages.error(request, '送信開始後または取消済みの予約は取り消せません。')
            return redirect('discord_scheduler:post_list')
        return render(request, 'discord_scheduler/post_cancel.html', {'post': post})

    def post(self, request, pk):
        post = get_object_or_404(ScheduledDiscordPost, pk=pk)
        updated = ScheduledDiscordPost.objects.filter(
            pk=post.pk, status=ScheduledDiscordPost.Status.SCHEDULED,
        ).update(
            status=ScheduledDiscordPost.Status.CANCELLED,
            updated_at=timezone.now(),
        )
        if updated:
            messages.success(request, '投稿予約を取り消しました。')
        else:
            messages.error(request, '送信が開始されたか、予約が取り消されたため取り消せませんでした。')
        return redirect('discord_scheduler:post_list')


@csrf_exempt
@require_POST
def process_posts(request):
    """Cloud Scheduler等から呼ぶ。セッションクッキーでの認証は受け付けない。"""
    expected = getattr(settings, 'REQUEST_TOKEN', '') or ''
    supplied = request.headers.get('Request-Token', '')
    if not expected or not constant_time_compare(supplied, expected):
        return HttpResponse('Unauthorized', status=401)
    return JsonResponse(process_scheduled_posts(limit=20))
