"""記事化 OK の発表の記事を、Cloud Scheduler からの呼び出しごとに少しずつ自動生成する。

生成待ちの印（EventDetail.article_generation_requested_at）は event.signals が付ける。
1 回の呼び出しで処理するのは 1〜2 件だけにし、Cloud Run のタイムアウト内に収める。
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from event.models import EventDetail
from event.notifications import notify_applicant_of_article_published
from event.services.content_generation_service import BlogOutput, collect_blog_sources, generate_blog
from event.services.media_service import ensure_pdf_thumbnail

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 1
MAX_BATCH_SIZE = 2
# 2 件目に着手するのはここまで。uWSGI の http-timeout（120 秒）内に収める
BATCH_TIME_BUDGET_SECONDS = 45
MAX_ATTEMPTS = 5
# 失敗後の再試行は 10・20・40・80 分後。字幕が付くまで待てるよう間隔を空ける
RETRY_BASE_DELAY = timedelta(minutes=10)
# 処理中の締切。過ぎても終わっていなければ（プロセスが落ちた等）次の呼び出しで拾い直す
CLAIM_LEASE = timedelta(minutes=15)
CLAIM_SCAN_LIMIT = 10
LAST_ERROR_MAX_LENGTH = 255

GENERATED = 'generated'
FAILED = 'failed'
SKIPPED_MANUAL = 'skipped_manual'
SKIPPED = 'skipped'
OUTCOMES = (GENERATED, FAILED, SKIPPED_MANUAL, SKIPPED)


@dataclass(frozen=True)
class ArticleGenerationResult:
    """1 件の処理結果。応答 JSON とログにそのまま出す。"""

    event_detail_id: int
    outcome: str
    reason: str = ''
    attempts: int = 0
    gave_up: bool = False


class ArticleGenerationError(Exception):
    """再試行の対象になる生成の失敗。reason は記録とログに使う短い識別子。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def process_article_generation_queue(limit: int = DEFAULT_BATCH_SIZE) -> dict:
    """期限の来た生成待ちを古い順に最大 limit 件処理し、件数と結果を返す。"""
    results: list[ArticleGenerationResult] = []
    started = time.monotonic()
    while len(results) < limit:
        if results and time.monotonic() - started > BATCH_TIME_BUDGET_SECONDS:
            break
        claimed = _claim_next(timezone.now())
        if claimed is None:
            break
        results.append(_process_claimed(*claimed))

    counts = {outcome: sum(1 for r in results if r.outcome == outcome) for outcome in OUTCOMES}
    counts['processed'] = len(results)
    counts['pending'] = EventDetail.objects.filter(article_generation_requested_at__isnull=False).count()
    logger.info('article_generation_run', extra={'event_type': 'article_generation_run', **counts})
    return {**counts, 'results': [asdict(result) for result in results]}


def _claim_next(now: datetime) -> tuple[EventDetail, datetime] | None:
    """期限の来た生成待ちを 1 件取り、処理中の締切を入れて他の呼び出しと重ならないようにする。"""
    candidates = (
        EventDetail.objects.filter(article_generation_requested_at__lte=now)
        .order_by('article_generation_requested_at', 'pk')
        .values_list('pk', 'article_generation_requested_at')[:CLAIM_SCAN_LIMIT]
    )
    lease_until = now + CLAIM_LEASE
    for pk, requested_at in candidates:
        claimed = EventDetail.objects.filter(
            pk=pk, article_generation_requested_at=requested_at,
        ).update(
            article_generation_requested_at=lease_until,
            article_generation_attempts=F('article_generation_attempts') + 1,
        )
        if claimed:
            detail = EventDetail.objects.select_related('event__community', 'applicant').get(pk=pk)
            return detail, lease_until
    return None


def _process_claimed(detail: EventDetail, lease_until: datetime) -> ArticleGenerationResult:
    """取った 1 件を生成し、結果を記録する。"""
    skip_reason = _skip_reason(detail)
    if skip_reason:
        _release(detail.pk, lease_until, reset_attempts=True)
        outcome = SKIPPED_MANUAL if skip_reason == 'manual_edit' else SKIPPED
        return _log_result(detail, outcome, skip_reason)
    if detail.article_generation_attempts > MAX_ATTEMPTS:
        # 処理中にプロセスが落ち続ける等で、締切切れの拾い直しが上限を超えた
        _release(detail.pk, lease_until, error='max_attempts')
        return _log_result(detail, FAILED, 'max_attempts', gave_up=True)

    try:
        blog_output = _generate(detail)
    except ArticleGenerationError as error:
        return _record_failure(detail, lease_until, error.reason)
    except Exception as error:
        logger.exception(
            'article_generation_error',
            extra={'event_type': 'article_generation_error', 'event_detail_id': detail.pk},
        )
        return _record_failure(detail, lease_until, f'error:{type(error).__name__}')

    outcome, reason = _store_article(detail, lease_until, blog_output)
    if outcome == GENERATED:
        _notify_published(detail.pk)
    return _log_result(detail, outcome, reason)


def _skip_reason(detail: EventDetail) -> str:
    """生成しない理由。生成してよければ空文字。"""
    if not detail.can_auto_generate_article:
        return 'not_eligible'
    state = detail.article_state()
    if state == EventDetail.ArticleState.MANUAL:
        return 'manual_edit'
    recorded_sources = (detail.article_source_video_id, detail.article_source_slide_name)
    if state == EventDetail.ArticleState.AUTO and detail.article_sources() == recorded_sources:
        return 'unchanged'
    return ''


def _generate(detail: EventDetail) -> BlogOutput:
    """その時点で揃っている字幕と PDF をすべて使って記事を作る。"""
    sources = collect_blog_sources(detail)
    if not sources.has_text:
        # 字幕がまだ付いていない動画や、文字の無い PDF。入力無しでは記事を作らない
        raise ArticleGenerationError('no_source_text')
    blog_output = generate_blog(detail, model=settings.GEMINI_MODEL, sources=sources)
    if not blog_output.title:
        raise ArticleGenerationError('empty_output')
    return blog_output


def _store_article(detail: EventDetail, lease_until: datetime, blog_output: BlogOutput) -> tuple[str, str]:
    """生成した記事を書き込む。処理中に編集・同意の変更・新しい入力があれば書かない。"""
    if not detail.thumbnail_image:
        # PDF の処理は時間がかかるので、行ロックを取る前に済ませる
        ensure_pdf_thumbnail(detail)

    with transaction.atomic():
        # 処理中に論理削除された発表も取り、対象外として印を外す（objects だと取れずに落ちる）
        current = EventDetail.all_objects.select_for_update().get(pk=detail.pk)
        if current.article_generation_requested_at != lease_until:
            # 処理中に新しい入力が来た（次の呼び出しで作り直す）か、手動の生成で印が外れた
            return SKIPPED, 'superseded'
        if not current.can_auto_generate_article:
            _release(current.pk, lease_until, reset_attempts=True)
            return SKIPPED, 'not_eligible'
        if current.article_state() == EventDetail.ArticleState.MANUAL:
            _release(current.pk, lease_until, reset_attempts=True)
            return SKIPPED_MANUAL, 'manual_edit'
        _write_article(current, blog_output, thumbnail_source=detail)
    return GENERATED, ''


def _write_article(current: EventDetail, blog_output: BlogOutput, *, thumbnail_source: EventDetail) -> None:
    """記事と生成元・本文のハッシュを保存し、生成待ちの印を外す。"""
    current.h1 = blog_output.title
    current.contents = blog_output.text
    current.meta_description = blog_output.meta_description
    update_fields = ['h1', 'contents', 'meta_description', 'updated_at']
    if not current.thumbnail_image and thumbnail_source.thumbnail_image:
        current.thumbnail_image = thumbnail_source.thumbnail_image.name
        update_fields.append('thumbnail_image')
    update_fields += current.record_generated_article()
    current.save(update_fields=update_fields)


def _record_failure(detail: EventDetail, lease_until: datetime, reason: str) -> ArticleGenerationResult:
    """失敗を記録し、上限までは間を空けて再試行する。上限に達したら印を外す。"""
    attempts = detail.article_generation_attempts
    gave_up = attempts >= MAX_ATTEMPTS
    retry_at = None if gave_up else timezone.now() + RETRY_BASE_DELAY * (2 ** max(attempts - 1, 0))
    EventDetail.all_objects.filter(pk=detail.pk, article_generation_requested_at=lease_until).update(
        article_generation_requested_at=retry_at,
        article_generation_last_error=reason[:LAST_ERROR_MAX_LENGTH],
    )
    return _log_result(detail, FAILED, reason, gave_up=gave_up)


def _release(pk: int, lease_until: datetime, *, error: str = '', reset_attempts: bool = False) -> None:
    """生成待ちの印を外す。処理中の締切が自分の入れたものの時だけ（新しい印は残す）。"""
    fields: dict = {'article_generation_requested_at': None}
    if error:
        fields['article_generation_last_error'] = error
    if reset_attempts:
        fields['article_generation_attempts'] = 0
    EventDetail.all_objects.filter(pk=pk, article_generation_requested_at=lease_until).update(**fields)


def _notify_published(event_detail_id: int) -> None:
    """発表者に記事の公開を知らせる。通知の失敗は生成の成功を覆さない。"""
    try:
        detail = EventDetail.objects.select_related('event__community', 'applicant').get(pk=event_detail_id)
        notify_applicant_of_article_published(detail)
    except Exception:
        logger.exception(
            'article_published_notification_failed',
            extra={'event_type': 'article_published_notification_failed', 'event_detail_id': event_detail_id},
        )


def _log_result(detail: EventDetail, outcome: str, reason: str = '', *, gave_up: bool = False) -> ArticleGenerationResult:
    """1 件の結果を構造化ログに出して返す。"""
    result = ArticleGenerationResult(
        event_detail_id=detail.pk,
        outcome=outcome,
        reason=reason,
        attempts=detail.article_generation_attempts,
        gave_up=gave_up,
    )
    log = logger.warning if outcome == FAILED else logger.info
    log('article_generation_result', extra={'event_type': 'article_generation_result', **asdict(result)})
    return result
