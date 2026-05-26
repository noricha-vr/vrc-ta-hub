# twitter/views.py
import html
import logging
import os
import threading
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.mixins import UserPassesTestMixin
from django.db import connections, models  # noqa: F401 - 既存テストの patch パス互換用
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_http_methods
from django.views.generic import CreateView, UpdateView, ListView, DeleteView, DetailView, TemplateView

logger = logging.getLogger(__name__)

from community.models import Community, CommunityMember
from event.models import Event
from .forms import TwitterTemplateForm
from .models import TwitterTemplate, TweetQueue
from .notifications import notify_tweet_post_failure
from .scheduling import default_scheduled_at
from .utils import format_event_info, generate_tweet, generate_tweet_url
from .services.media_service import upload_media_to_x as upload_media
from .services.tweet_scheduling_service import (
    post_tweet_queue_item,
    process_scheduled_tweets,
    retry_generation,
    retry_generation_async,
)
from .services.x_api_service import post_tweet_to_x as post_tweet

TWEET_QUEUE_PAGINATE_BY = 20
SCHEDULED_MINUTE_CHOICES = {0, 30}
SCHEDULED_AT_MINUTE_ERROR = '予約日時は00分または30分で指定してください。'
JST = ZoneInfo("Asia/Tokyo")


class TwitterTemplateBaseView(LoginRequiredMixin, UserPassesTestMixin):
    model = TwitterTemplate
    form_class = TwitterTemplateForm
    template_name = 'twitter/twitter_template_form.html'

    def get_active_community(self):
        """セッションからアクティブな集会を取得"""
        community_id = self.request.session.get('active_community_id')
        if not community_id:
            return None
        community = Community.objects.filter(id=community_id).first()
        if community and community.is_manager(self.request.user):
            return community
        return None

    def test_func(self):
        community = self.get_active_community()
        return community is not None

    def get_success_url(self):
        return reverse_lazy('twitter:template_list')

    def form_valid(self, form):
        community = self.get_active_community()
        if not community:
            raise Http404("集会が選択されていないか、権限がありません")
        form.instance.community = community
        return super().form_valid(form)


class TwitterTemplateCreateView(TwitterTemplateBaseView, CreateView):
    pass


class TwitterTemplateUpdateView(TwitterTemplateBaseView, UpdateView):
    def test_func(self):
        if not super().test_func():
            return False
        twitter_template = self.get_object()
        return twitter_template.community.is_manager(self.request.user)


class TwitterTemplateListView(LoginRequiredMixin, ListView):
    model = TwitterTemplate
    template_name = 'twitter/twitter_template_list.html'
    context_object_name = 'templates'

    def get_queryset(self):
        """セッションからactive_community_idを取得してテンプレートを絞り込む"""
        community_id = self.request.session.get('active_community_id')
        if not community_id:
            return TwitterTemplate.objects.none()

        community = get_object_or_404(Community, id=community_id)

        # メンバーシップ権限チェック
        if not community.is_manager(self.request.user):
            return TwitterTemplate.objects.none()

        return TwitterTemplate.objects.filter(community=community)


class TweetEventView(View):
    def get(self, request, event_pk, template_pk):
        event = get_object_or_404(Event, pk=event_pk)
        template = get_object_or_404(TwitterTemplate, pk=template_pk, community=event.community)
        tweet_url = generate_tweet_url(event, template)
        return redirect(tweet_url)


class TwitterTemplateDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = TwitterTemplate
    success_url = reverse_lazy('twitter:template_list')

    def test_func(self):
        template = self.get_object()
        return self.request.user.is_superuser or template.community.is_manager(self.request.user)

    def form_valid(self, form):
        self.object.delete()
        messages.success(self.request, 'テンプレートが削除されました。')
        return JsonResponse({'success': True})


class TweetEventWithTemplateView(TemplateView):
    """ポストプレビュー画面を表示するビュー"""
    template_name = 'twitter/tweet_preview.html'

    TWITTER_INTENT_BASE_URL = "https://twitter.com/intent/tweet?text="

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event = get_object_or_404(Event, pk=self.kwargs['event_pk'])
        template = get_object_or_404(TwitterTemplate, pk=self.kwargs['template_pk'])

        # Format event info before generating tweet
        event_info = format_event_info(event)
        raw_tweet_text = generate_tweet(template.template, event_info)

        # Add debug logging
        logger.debug(f"Generated tweet_text: {raw_tweet_text}")

        # intent URL用にURLエンコード
        intent_url = ""
        if raw_tweet_text:
            encoded_text = urllib.parse.quote(raw_tweet_text)
            intent_url = f"{self.TWITTER_INTENT_BASE_URL}{encoded_text}"

        # Replace newlines with HTML line breaks for display
        tweet_text = html.escape(raw_tweet_text).replace('\n', '<br>') if raw_tweet_text else ""

        context.update({
            'tweet_text': tweet_text,
            'raw_tweet_text': raw_tweet_text,
            'intent_url': intent_url,
            'event': event,
            'template': template,
        })
        return context


def _retry_generation(queue_item: TweetQueue) -> None:
    """生成失敗キューのテキスト生成をリトライする."""
    retry_generation(queue_item)


def _retry_generation_async(queue_id: int) -> None:
    """バックグラウンドスレッドで再生成し、終了時にDB接続を解放する."""
    retry_generation_async(
        queue_id,
        retry_func=_retry_generation,
        close_connections_func=connections.close_all,
    )


def _post_tweet_queue_item(
    queue_item: TweetQueue,
    *,
    failure_status: str | None = 'failed',
) -> dict[str, object]:
    """TweetQueue 1件を X API に投稿し、保存前の結果を返す."""
    return post_tweet_queue_item(
        queue_item,
        failure_status=failure_status,
        post_tweet_func=post_tweet,
        upload_media_func=upload_media,
        notify_failure_func=notify_tweet_post_failure,
    )


@require_http_methods(["GET"])
def post_scheduled_tweets(request):
    """Cloud Scheduler から 1 分ごとに呼ばれるエンドポイント。

    Phase 1: 生成失敗/停滞キューのリトライ
    Phase 2: ready キューを最大 1 件投稿
    """
    request_token = request.headers.get("Request-Token", "")
    if request_token != os.environ.get("REQUEST_TOKEN", ""):
        return HttpResponse("Unauthorized", status=401)

    return JsonResponse(
        process_scheduled_tweets(
            post_tweet_func=post_tweet,
            upload_media_func=upload_media,
            notify_failure_func=notify_tweet_post_failure,
        ),
    )


# --- TweetQueue 管理ビュー (superuser only) ---


class SuperuserRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """スーパーユーザーのみアクセスを許可する Mixin。"""

    def test_func(self):
        return self.request.user.is_superuser


class TweetQueueViewerMixin(LoginRequiredMixin, UserPassesTestMixin):
    """TweetQueue 閲覧の権限制御 Mixin。

    superuser、または CommunityMember として何らかの集会に所属しているユーザー
    （主催者・スタッフ）のみアクセス可能。
    """

    def test_func(self):
        user = self.request.user
        if user.is_superuser:
            return True
        return CommunityMember.objects.filter(user=user).exists()


def _scope_tweet_queue_to_user(qs, user):
    """superuser 以外には、所属コミュニティの TweetQueue のみを返すよう絞り込む。"""
    if user.is_superuser:
        return qs
    user_community_ids = CommunityMember.objects.filter(
        user=user,
    ).values_list('community_id', flat=True)
    return qs.filter(community_id__in=list(user_community_ids))


def _is_scheduled_today_jst(scheduled_at, now=None):
    """予約日時が JST 基準の今日に含まれるかを返す。"""
    if scheduled_at is None:
        return False
    current = now or timezone.now()
    today_jst = timezone.localtime(current, JST).date()
    scheduled_date_jst = timezone.localtime(scheduled_at, JST).date()
    return scheduled_date_jst == today_jst


class TweetQueueListView(TweetQueueViewerMixin, ListView):
    """TweetQueue 一覧ページ。ステータスフィルタとページネーション付き。

    superuser は全件、主催者・スタッフは自分が所属する集会の分のみ閲覧可能。
    """

    model = TweetQueue
    template_name = 'twitter/tweet_queue_list.html'
    context_object_name = 'tweet_queues'
    paginate_by = TWEET_QUEUE_PAGINATE_BY
    SORT_FIELDS = {
        'created_at': 'created_at',
        'scheduled_at': 'scheduled_at',
        'posted_at': 'posted_at',
    }
    DEFAULT_SORT_FIELD = 'scheduled_at'
    DEFAULT_SORT_ORDER = 'desc'

    def _get_sort_field(self):
        sort = self.request.GET.get('sort', self.DEFAULT_SORT_FIELD)
        if sort in self.SORT_FIELDS:
            return sort
        return self.DEFAULT_SORT_FIELD

    def _get_sort_order(self):
        order = self.request.GET.get('order', self.DEFAULT_SORT_ORDER)
        if order in {'asc', 'desc'}:
            return order
        return self.DEFAULT_SORT_ORDER

    def _get_ordering(self):
        sort_field = self._get_sort_field()
        sort_order = self._get_sort_order()
        field_name = self.SORT_FIELDS[sort_field]
        direction = 'asc' if sort_order == 'asc' else 'desc'
        primary_order = getattr(models.F(field_name), direction)(nulls_last=True)
        return [primary_order, models.F('created_at').desc(), models.F('pk').desc()]

    def get_queryset(self):
        qs = TweetQueue.objects.select_related('community', 'event').order_by(*self._get_ordering())
        qs = _scope_tweet_queue_to_user(qs, self.request.user)
        status = self.request.GET.get('status', '')
        valid_statuses = {choice[0] for choice in TweetQueue.STATUS_CHOICES}
        if status in valid_statuses:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        status = self.request.GET.get('status', '')
        current_sort = self._get_sort_field()
        current_order = self._get_sort_order()
        context['current_status'] = status
        context['status_choices'] = TweetQueue.STATUS_CHOICES
        context['current_sort'] = current_sort
        context['current_order'] = current_order

        pagination_params = self.request.GET.copy()
        pagination_params.pop('page', None)
        context['current_query_params'] = pagination_params.urlencode()

        header_links = {}
        for sort_key in self.SORT_FIELDS:
            query_params = self.request.GET.copy()
            query_params.pop('page', None)
            query_params['sort'] = sort_key
            if current_sort == sort_key and current_order == 'asc':
                query_params['order'] = 'desc'
            else:
                query_params['order'] = 'asc'
            header_links[sort_key] = query_params.urlencode()
        context['sort_links'] = header_links
        now = timezone.now()
        context['today_tweet_queue_ids'] = {
            item.pk
            for item in context['page_obj'].object_list
            if _is_scheduled_today_jst(item.scheduled_at, now=now)
        }
        return context


class TweetQueueDetailView(TweetQueueViewerMixin, DetailView):
    """TweetQueue 詳細・編集ページ。

    閲覧（GET）は superuser または所属コミュニティを持つスタッフ以上が可能。
    編集系の POST アクション（update / retry / post_now / delete）は superuser のみ。
    """

    model = TweetQueue
    template_name = 'twitter/tweet_queue_detail.html'
    context_object_name = 'object'

    def get_queryset(self):
        qs = TweetQueue.objects.select_related('community', 'event', 'event_detail')
        return _scope_tweet_queue_to_user(qs, self.request.user)

    def post(self, request, *args, **kwargs):
        # 編集系アクションは superuser のみ許可（スタッフは閲覧のみ）
        if not request.user.is_superuser:
            return HttpResponseForbidden('編集はスーパーユーザーのみ可能です')

        self.object = self.get_object()
        action = request.POST.get('action', 'update')

        if action == 'retry':
            return self._handle_retry()
        elif action == 'post_now':
            return self._handle_post_now()
        elif action == 'delete':
            return self._handle_delete()
        else:
            return self._handle_update(request)

    def _handle_update(self, request):
        """generated_text と image_url と scheduled_at を更新する。"""
        generated_text = request.POST.get('generated_text', '')
        image_url = request.POST.get('image_url', '')
        scheduled_at_raw = request.POST.get('scheduled_at', '').strip()

        if scheduled_at_raw:
            parsed = parse_datetime(scheduled_at_raw)
            if parsed is None:
                try:
                    parsed = datetime.fromisoformat(scheduled_at_raw)
                except ValueError:
                    messages.error(request, SCHEDULED_AT_MINUTE_ERROR)
                    return redirect(reverse('twitter:tweet_queue_detail', kwargs={'pk': self.object.pk}))
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            parsed = parsed.replace(second=0, microsecond=0)
            if parsed.minute not in SCHEDULED_MINUTE_CHOICES:
                messages.error(request, SCHEDULED_AT_MINUTE_ERROR)
                return redirect(reverse('twitter:tweet_queue_detail', kwargs={'pk': self.object.pk}))
            scheduled_at = parsed
        else:
            scheduled_at = default_scheduled_at(
                tweet_type=self.object.tweet_type,
                event=self.object.event,
            )

        self.object.generated_text = generated_text
        self.object.image_url = image_url
        self.object.scheduled_at = scheduled_at
        self.object.save(update_fields=['generated_text', 'image_url', 'scheduled_at'])
        messages.success(request, '保存しました。')
        return redirect(reverse('twitter:tweet_queue_detail', kwargs={'pk': self.object.pk}))

    def _handle_delete(self):
        """キューを削除する。"""
        pk = self.object.pk
        self.object.delete()
        messages.success(self.request, f'キュー #{pk} を削除しました。')
        return redirect(reverse('twitter:tweet_queue_list'))

    def _handle_retry(self):
        """generating に戻してバックグラウンドでテキスト再生成を開始する。"""
        if self.object.status not in ('generation_failed', 'generating'):
            messages.error(self.request, 'このステータスではリトライできません。')
            return redirect(reverse('twitter:tweet_queue_detail', kwargs={'pk': self.object.pk}))

        self.object.status = 'generating'
        self.object.error_message = ''
        self.object.save(update_fields=['status', 'error_message'])

        thread = threading.Thread(
            target=_retry_generation_async,
            args=(self.object.pk,),
            daemon=True,
        )
        thread.start()

        messages.info(self.request, 'テキスト再生成を開始しました。')
        return redirect(reverse('twitter:tweet_queue_detail', kwargs={'pk': self.object.pk}))

    def _handle_post_now(self):
        """未投稿キューを即座に投稿する。"""
        if self.object.status == 'posted':
            messages.error(self.request, '投稿済みのキューは再投稿できません。')
            return redirect(reverse('twitter:tweet_queue_detail', kwargs={'pk': self.object.pk}))
        if not self.object.generated_text.strip():
            messages.error(self.request, '生成テキストが空のため投稿できません。')
            return redirect(reverse('twitter:tweet_queue_detail', kwargs={'pk': self.object.pk}))

        result = _post_tweet_queue_item(self.object, failure_status=None)

        if result["status"] == "posted":
            self.object.save()
            messages.success(self.request, 'ポストを投稿しました。')
            logger.info("Manual tweet posted for queue %d: %s", self.object.pk, self.object.tweet_id)
        else:
            self.object.save(update_fields=['error_message'])
            messages.error(self.request, self.object.error_message)
            logger.warning("Manual tweet post failed for queue %d", self.object.pk)

        return redirect(reverse('twitter:tweet_queue_detail', kwargs={'pk': self.object.pk}))
