# twitter/views.py
import html
import logging
import os
import threading
import urllib.parse
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.mixins import UserPassesTestMixin
from django.db import IntegrityError, models, transaction
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_http_methods
from django.views.generic import CreateView, UpdateView, ListView, DeleteView, DetailView, TemplateView

logger = logging.getLogger(__name__)

from community.models import Community, CommunityMember
from event.models import Event
from .forms import TwitterTemplateForm
from .models import TwitterTemplate, TweetQueue
from .tweet_generator import get_generator, get_poster_image_url
from .utils import format_event_info, generate_tweet, generate_tweet_url
from .x_api import post_tweet, upload_media

TWEET_QUEUE_PAGINATE_BY = 20
REMINDER_DETAIL_TYPES = ("LT", "SPECIAL")
SAME_DAY_INDIVIDUAL_SKIP_REASON = '当日リマインドに統合したため個別告知は投稿しません'


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

    def delete(self, request, *args, **kwargs):
        """
        FormMixinを使用するため、deleteメソッドをオーバーライドして
        form_validメソッドを呼び出します。
        """
        self.object = self.get_object()
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        else:
            return self.form_invalid(form)


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
        queue_item.save()
        return

    if text:
        queue_item.generated_text = text
        queue_item.status = 'ready'
        queue_item.error_message = ''

        # 画像URLも設定（まだない場合）
        if not queue_item.image_url:
            image_url = get_poster_image_url(queue_item.community)
            if image_url:
                queue_item.image_url = image_url

        queue_item.save()
        logger.info("Retry generation succeeded for queue %d", queue_item.pk)
    else:
        queue_item.status = 'generation_failed'
        queue_item.error_message = 'リトライ生成にも失敗'
        queue_item.save()
        logger.error("Retry generation failed for queue %d", queue_item.pk)


def _create_daily_reminder_queues(today=None) -> int:
    """当日開催イベント向けの missing なリマインドキューを補完作成する。"""
    today = today or timezone.localdate()
    today_events = (
        Event.objects.filter(
            date=today,
            details__status='approved',
            details__detail_type__in=REMINDER_DETAIL_TYPES,
        )
        .select_related('community')
        .distinct()
    )

    created_count = 0
    for event in today_events:
        try:
            with transaction.atomic():
                queue_item, created = TweetQueue.objects.get_or_create(
                    event=event,
                    tweet_type='daily_reminder',
                    defaults={
                        'community': event.community,
                    },
                )
        except IntegrityError:
            logger.info("Skipped concurrent daily reminder tweet for event %d", event.pk)
            continue

        if not created:
            continue

        # 同じ 19:00 実行で投稿対象に含めるため、作成後すぐ生成まで進める。
        _retry_generation(queue_item)
        created_count += 1
        logger.info("Queued daily reminder tweet for event %d", event.pk)

    return created_count


@require_http_methods(["GET"])
def post_scheduled_tweets(request):
    """Cloud Scheduler から毎日 19:00 JST に呼ばれるエンドポイント。

    Phase 0: 当日イベントの missing なリマインドキュー作成
    Phase 1: 生成失敗/停滞キューのリトライ
    Phase 2: ready キューの投稿
    """
    request_token = request.headers.get("Request-Token", "")
    if request_token != os.environ.get("REQUEST_TOKEN", ""):
        return HttpResponse("Unauthorized", status=401)

    created_count = _create_daily_reminder_queues()

    # Phase 1: 生成リトライ（generation_failed + 1時間以上前の generating）
    retry_threshold = timezone.now() - timedelta(hours=RETRY_THRESHOLD_HOURS)
    retry_items = list(
        TweetQueue.objects.filter(
            models.Q(status='generation_failed')
            | models.Q(status='generating', created_at__lt=retry_threshold),
        ).select_related('community', 'event', 'event_detail')
    )
    retried_count = len(retry_items)

    for item in retry_items:
        _retry_generation(item)

    # Phase 2: ready キューの投稿
    ready_items = TweetQueue.objects.filter(status='ready').select_related(
        'community', 'event', 'event_detail',
    )

    results = []
    for queue_item in ready_items:
        # LT/特別回告知は、イベント日が過去ならスキップ（期限切れ防止）
        if queue_item.tweet_type in ('lt', 'special') and queue_item.event:
            if queue_item.event.date == timezone.localdate():
                queue_item.status = 'skipped'
                queue_item.error_message = SAME_DAY_INDIVIDUAL_SKIP_REASON
                queue_item.generated_text = ''
                queue_item.save(update_fields=['status', 'error_message', 'generated_text'])
                results.append({
                    "id": queue_item.pk, "status": "skipped", "reason": "same_day_integrated",
                })
                logger.info("Skipped same-day %s tweet for queue %d", queue_item.tweet_type, queue_item.pk)
                continue
            if queue_item.event.date < timezone.localdate():
                queue_item.status = 'failed'
                queue_item.error_message = 'イベント日が過去のため投稿スキップ'
                queue_item.save()
                results.append({
                    "id": queue_item.pk, "status": "skipped", "reason": "event_date_passed",
                })
                logger.info("Skipped expired %s tweet for queue %d", queue_item.tweet_type, queue_item.pk)
                continue

        if queue_item.tweet_type == 'daily_reminder' and queue_item.event:
            if queue_item.event.date != timezone.localdate():
                queue_item.status = 'failed'
                queue_item.error_message = '当日イベントではないため投稿スキップ'
                queue_item.save()
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
        response_data = post_tweet(queue_item.generated_text, media_ids=media_ids)

        if response_data:
            queue_item.status = 'posted'
            queue_item.tweet_id = response_data.get('id', '')
            queue_item.posted_at = timezone.now()
            results.append({
                "id": queue_item.pk, "status": "posted", "tweet_id": queue_item.tweet_id,
            })
            logger.info("Tweet posted for queue %d: %s", queue_item.pk, queue_item.tweet_id)
        else:
            queue_item.status = 'failed'
            queue_item.error_message = 'X API投稿に失敗'
            results.append({
                "id": queue_item.pk, "status": "failed", "error": "post_failed",
            })
            logger.warning("Tweet post failed for queue %d", queue_item.pk)

        queue_item.save()

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

    def get_queryset(self):
        qs = TweetQueue.objects.select_related('community', 'event').order_by('-created_at')
        qs = _scope_tweet_queue_to_user(qs, self.request.user)
        status = self.request.GET.get('status', '')
        valid_statuses = {choice[0] for choice in TweetQueue.STATUS_CHOICES}
        if status in valid_statuses:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        status = self.request.GET.get('status', '')
        context['current_status'] = status
        context['status_choices'] = TweetQueue.STATUS_CHOICES
        # ページネーション用にステータスフィルタをクエリパラメータとして保持
        if status:
            context['current_query_params'] = f'status={status}'
        else:
            context['current_query_params'] = ''
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
        """generated_text と image_url を更新する。"""
        self.object.generated_text = request.POST.get('generated_text', '')
        self.object.image_url = request.POST.get('image_url', '')
        self.object.save(update_fields=['generated_text', 'image_url'])
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
            target=_retry_generation,
            args=(self.object,),
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
        response_data = post_tweet(self.object.generated_text, media_ids=media_ids)

        if response_data:
            self.object.status = 'posted'
            self.object.tweet_id = response_data.get('id', '')
            self.object.posted_at = timezone.now()
            self.object.save()
            messages.success(self.request, 'ポストを投稿しました。')
            logger.info("Manual tweet posted for queue %d: %s", self.object.pk, self.object.tweet_id)
        else:
            self.object.status = 'failed'
            self.object.error_message = 'X API投稿に失敗'
            self.object.save()
            messages.error(self.request, '投稿に失敗しました。')
            logger.warning("Manual tweet post failed for queue %d", self.object.pk)

        return redirect(reverse('twitter:tweet_queue_detail', kwargs={'pk': self.object.pk}))
