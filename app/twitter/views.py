# twitter/views.py
import html
import logging
import os
import threading
import urllib.parse
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.mixins import UserPassesTestMixin
from django.db import connections, models
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
from .db import run_with_db_reconnect
from .forms import TwitterTemplateForm
from .models import TwitterTemplate, TweetQueue
from .notifications import notify_tweet_post_failure
from .scheduling import default_scheduled_at
from .tweet_generator import get_generator, get_tweet_image_url
from .utils import format_event_info, generate_tweet, generate_tweet_url
from .x_api import post_tweet, upload_media

TWEET_QUEUE_PAGINATE_BY = 20
SAME_DAY_INDIVIDUAL_SKIP_REASON = '当日リマインドに統合したため個別告知は投稿しません'
SCHEDULED_MINUTE_CHOICES = {0, 30}
SCHEDULED_AT_MINUTE_ERROR = '予約日時は00分または30分で指定してください。'


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


RETRY_THRESHOLD_HOURS = 1
SCHEDULE_EXPIRY_HOURS = 24
SCHEDULE_EXPIRED_SKIP_REASON = '予約日時から24時間以上経過したため投稿をスキップ'


def _retry_generation(queue_item) -> None:
    """生成失敗キューのテキスト生成をリトライする（同期）。

    成功時は status を ready に、失敗時は generation_failed に更新する。
    例外発生時も generation_failed に更新し、次のアイテムの処理に進む。
    """
    try:
        generator = get_generator(queue_item.tweet_type)
        text = generator(queue_item) if generator else None
    except Exception:
        logger.exception("Retry generation raised exception for queue %d", queue_item.pk)
        queue_item.status = 'generation_failed'
        queue_item.error_message = 'リトライ中に例外が発生'
        run_with_db_reconnect(
            queue_item.save,
            context=f"retry_generation_exception queue={queue_item.pk}",
        )
        return

    if text:
        queue_item.generated_text = text
        queue_item.status = 'ready'
        queue_item.error_message = ''

        # 画像URLも設定（まだない場合）
        if not queue_item.image_url:
            image_url = get_tweet_image_url(queue_item)
            if image_url:
                queue_item.image_url = image_url

        run_with_db_reconnect(
            queue_item.save,
            context=f"retry_generation_success queue={queue_item.pk}",
        )
        logger.info("Retry generation succeeded for queue %d", queue_item.pk)
    else:
        queue_item.status = 'generation_failed'
        queue_item.error_message = 'リトライ生成にも失敗'
        run_with_db_reconnect(
            queue_item.save,
            context=f"retry_generation_failed queue={queue_item.pk}",
        )
        logger.error("Retry generation failed for queue %d", queue_item.pk)


def _retry_generation_async(queue_id: int) -> None:
    """バックグラウンドスレッドで再生成し、終了時にDB接続を確実に解放する。"""
    try:
        try:
            queue_item = run_with_db_reconnect(
                lambda: TweetQueue.objects.select_related(
                    'community', 'event', 'event_detail',
                ).get(pk=queue_id),
                context=f"retry_generation_fetch queue={queue_id}",
            )
        except TweetQueue.DoesNotExist:
            logger.error("TweetQueue %d not found for retry generation", queue_id)
            return

        _retry_generation(queue_item)
    except Exception:
        logger.exception("Async retry generation failed for queue %d", queue_id)
    finally:
        connections.close_all()


@require_http_methods(["GET"])
def post_scheduled_tweets(request):
    """Cloud Scheduler から 30 分ごとに呼ばれるエンドポイント。

    Phase 1: 生成失敗/停滞キューのリトライ
    Phase 2: ready キューの投稿
    """
    request_token = request.headers.get("Request-Token", "")
    if request_token != os.environ.get("REQUEST_TOKEN", ""):
        return HttpResponse("Unauthorized", status=401)

    created_count = 0
    now = timezone.now()
    expiry_threshold = now - timedelta(hours=SCHEDULE_EXPIRY_HOURS)

    overdue_items = run_with_db_reconnect(
        lambda: list(
            TweetQueue.objects.filter(
                status__in=('generating', 'generation_failed', 'ready'),
                scheduled_at__lt=expiry_threshold,
            ).select_related('community', 'event', 'event_detail'),
        ),
        context="post_scheduled_tweets_fetch_overdue",
    )

    results = []
    for item in overdue_items:
        item.status = 'skipped'
        item.error_message = SCHEDULE_EXPIRED_SKIP_REASON
        run_with_db_reconnect(
            lambda: item.save(update_fields=['status', 'error_message']),
            context=f"post_scheduled_tweets_skip_expired queue={item.pk}",
        )
        results.append({
            "id": item.pk, "status": "skipped", "reason": "schedule_expired",
        })
        logger.info("Skipped expired scheduled tweet for queue %d", item.pk)

    # Phase 1: 生成リトライ（generation_failed + 1時間以上前の generating）
    retry_threshold = now - timedelta(hours=RETRY_THRESHOLD_HOURS)
    retry_items = run_with_db_reconnect(
        lambda: list(
            TweetQueue.objects.filter(
                (
                    models.Q(status='generation_failed')
                    | models.Q(status='generating', created_at__lt=retry_threshold)
                ),
            ).select_related('community', 'event', 'event_detail'),
        ),
        context="post_scheduled_tweets_fetch_retry",
    )
    retried_count = len(retry_items)

    for item in retry_items:
        _retry_generation(item)

    # Phase 2: ready キューの投稿
    ready_items = run_with_db_reconnect(
        lambda: list(
            TweetQueue.objects.filter(
                status='ready',
                scheduled_at__lte=now,
            ).select_related(
                'community', 'event', 'event_detail',
            ),
        ),
        context="post_scheduled_tweets_fetch_ready",
    )
    for queue_item in ready_items:
        # LT/特別回告知は、イベント日が過去ならスキップ（期限切れ防止）
        if queue_item.tweet_type in ('lt', 'special') and queue_item.event:
            if queue_item.event.date == timezone.localdate():
                queue_item.status = 'skipped'
                queue_item.error_message = SAME_DAY_INDIVIDUAL_SKIP_REASON
                queue_item.generated_text = ''
                run_with_db_reconnect(
                    lambda: queue_item.save(
                        update_fields=['status', 'error_message', 'generated_text'],
                    ),
                    context=f"post_scheduled_tweets_skip_same_day queue={queue_item.pk}",
                )
                results.append({
                    "id": queue_item.pk, "status": "skipped", "reason": "same_day_integrated",
                })
                logger.info("Skipped same-day %s tweet for queue %d", queue_item.tweet_type, queue_item.pk)
                continue
            if queue_item.event.date < timezone.localdate():
                queue_item.status = 'failed'
                queue_item.error_message = 'イベント日が過去のため投稿スキップ'
                run_with_db_reconnect(
                    queue_item.save,
                    context=f"post_scheduled_tweets_skip_past_event queue={queue_item.pk}",
                )
                results.append({
                    "id": queue_item.pk, "status": "skipped", "reason": "event_date_passed",
                })
                logger.info("Skipped expired %s tweet for queue %d", queue_item.tweet_type, queue_item.pk)
                continue

        if queue_item.tweet_type == 'daily_reminder' and queue_item.event:
            if queue_item.event.date != timezone.localdate():
                queue_item.status = 'failed'
                queue_item.error_message = '当日イベントではないため投稿スキップ'
                run_with_db_reconnect(
                    queue_item.save,
                    context=f"post_scheduled_tweets_skip_stale_daily queue={queue_item.pk}",
                )
                results.append({
                    "id": queue_item.pk, "status": "skipped", "reason": "not_event_day",
                })
                logger.info("Skipped stale daily reminder tweet for queue %d", queue_item.pk)
                continue

        # 画像アップロード
        media_ids = None
        if queue_item.image_url:
            media_id = upload_media(queue_item.image_url)
            if media_id:
                media_ids = [media_id]

        # ツイート投稿
        result = post_tweet(queue_item.generated_text, media_ids=media_ids)

        if result["ok"]:
            queue_item.status = 'posted'
            queue_item.tweet_id = (result["data"] or {}).get('id', '')
            queue_item.posted_at = timezone.now()
            results.append({
                "id": queue_item.pk, "status": "posted", "tweet_id": queue_item.tweet_id,
            })
            logger.info("Tweet posted for queue %d: %s", queue_item.pk, queue_item.tweet_id)
        else:
            queue_item.status = 'failed'
            status_code = result.get("status_code")
            queue_item.error_message = (
                f'X API投稿に失敗 (status={status_code})' if status_code else 'X API投稿に失敗'
            )
            results.append({
                "id": queue_item.pk, "status": "failed", "error": "post_failed",
            })
            logger.warning("Tweet post failed for queue %d", queue_item.pk)
            notify_tweet_post_failure(queue_item, result)

        run_with_db_reconnect(
            queue_item.save,
            context=f"post_scheduled_tweets_save_result queue={queue_item.pk}",
        )

    return JsonResponse({
        "created": created_count,
        "retried": retried_count,
        "processed": len(results),
        "results": results,
    })


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
        """ready キューを即座に投稿する。"""
        if self.object.status != 'ready':
            messages.error(self.request, '投稿待ちステータスのキューのみ投稿できます。')
            return redirect(reverse('twitter:tweet_queue_detail', kwargs={'pk': self.object.pk}))

        # 画像アップロード
        media_ids = None
        if self.object.image_url:
            media_id = upload_media(self.object.image_url)
            if media_id:
                media_ids = [media_id]

        # ツイート投稿
        result = post_tweet(self.object.generated_text, media_ids=media_ids)

        if result["ok"]:
            self.object.status = 'posted'
            self.object.tweet_id = (result["data"] or {}).get('id', '')
            self.object.posted_at = timezone.now()
            self.object.save()
            messages.success(self.request, 'ポストを投稿しました。')
            logger.info("Manual tweet posted for queue %d: %s", self.object.pk, self.object.tweet_id)
        else:
            self.object.status = 'failed'
            status_code = result.get("status_code")
            self.object.error_message = (
                f'X API投稿に失敗 (status={status_code})' if status_code else 'X API投稿に失敗'
            )
            self.object.save()
            messages.error(self.request, '投稿に失敗しました。')
            logger.warning("Manual tweet post failed for queue %d", self.object.pk)
            notify_tweet_post_failure(self.object, result)

        return redirect(reverse('twitter:tweet_queue_detail', kwargs={'pk': self.object.pk}))
