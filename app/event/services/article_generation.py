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

from event.material_upload_reminders import get_material_reminder_recipient
from event.models import EventDetail
from event.notifications import notify_applicant_of_article_published
from event.services.content_generation_service import (
    BlogOutput,
    BlogSources,
    collect_blog_sources,
    fetch_transcript,
    generate_blog,
    set_generated_article,
)
from event.services.media_service import ensure_pdf_thumbnail

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 1
MAX_BATCH_SIZE = 2
# 1 件目を終えた時にこれを過ぎていたら 2 件目を始めない。uWSGI の http-timeout（120 秒）内に収める
BATCH_TIME_BUDGET_SECONDS = 30
# 最後の回は、字幕を待たずに取れた入力だけで作る
MAX_ATTEMPTS = 5
# 失敗・字幕待ちの再試行は 10・20・40・80 分後。字幕が付くまで待てるよう間隔を空ける
RETRY_BASE_DELAY = timedelta(minutes=10)
# 処理中の締切。過ぎても終わっていなければ（プロセスが落ちた等）次の呼び出しで拾い直す
CLAIM_LEASE = timedelta(minutes=15)
CLAIM_SCAN_LIMIT = 10
LAST_ERROR_MAX_LENGTH = 255

GENERATED = 'generated'
DEFERRED = 'deferred'
FAILED = 'failed'
SKIPPED_MANUAL = 'skipped_manual'
SKIPPED = 'skipped'
OUTCOMES = (GENERATED, DEFERRED, FAILED, SKIPPED_MANUAL, SKIPPED)


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


class ArticleGenerationDeferred(ArticleGenerationError):
    """入力がそろうのを待つ（動画の字幕がまだ無い）。失敗ではないので結果は deferred にする。"""


def _monotonic() -> float:
    """経過時間の計測に使う時計（テストで差し替える）。"""
    return time.monotonic()


def process_article_generation_queue(limit: int = DEFAULT_BATCH_SIZE) -> dict:
    """期限の来た生成待ちを古い順に最大 limit 件処理し、件数と結果を返す。

    1 件目を終えた時点で BATCH_TIME_BUDGET_SECONDS を過ぎていたら、2 件目は始めない。
    """
    results: list[ArticleGenerationResult] = []
    started = _monotonic()
    while len(results) < limit:
        if results and _monotonic() - started > BATCH_TIME_BUDGET_SECONDS:
            break
        claimed = _claim_next(timezone.now())
        if claimed is None:
            break
        results.append(_process_one(*claimed))

    counts = {outcome: sum(1 for r in results if r.outcome == outcome) for outcome in OUTCOMES}
    counts['processed'] = len(results)
    counts['pending'] = EventDetail.objects.filter(article_generation_requested_at__isnull=False).count()
    logger.info('article_generation_run', extra={'event_type': 'article_generation_run', **counts})
    return {**counts, 'results': [asdict(result) for result in results]}


def _due_candidates(now: datetime) -> list[tuple[int, datetime | None]]:
    """期限の来た生成待ち（pk, 印の時刻）を古い順に返す（印は lte で絞るので None にはならない）。"""
    return list(
        EventDetail.objects.filter(article_generation_requested_at__lte=now)
        .order_by('article_generation_requested_at', 'pk')
        .values_list('pk', 'article_generation_requested_at')[:CLAIM_SCAN_LIMIT]
    )


def _try_claim(pk: int, requested_at: datetime | None, lease_until: datetime) -> bool:
    """印が変わっていなければ処理中の締切を入れて取る。他の呼び出しと重ならないようにする。"""
    return bool(
        EventDetail.objects.filter(pk=pk, article_generation_requested_at=requested_at).update(
            article_generation_requested_at=lease_until,
            article_generation_attempts=F('article_generation_attempts') + 1,
        )
    )


def _claim_next(now: datetime) -> tuple[int, datetime] | None:
    """期限の来た生成待ちを 1 件取り、（pk, 処理中の締切）を返す。"""
    lease_until = now + CLAIM_LEASE
    for pk, requested_at in _due_candidates(now):
        if _try_claim(pk, requested_at, lease_until):
            return pk, lease_until
    return None


def _process_one(pk: int, lease_until: datetime) -> ArticleGenerationResult:
    """取った 1 件を処理する。DB エラーなど予期しない例外も失敗として記録し、バッチは続ける。"""
    try:
        # 取った直後に論理削除されることもあるので all_objects で読む（削除済みは対象外としてスキップ）
        detail = EventDetail.all_objects.select_related('event__community', 'applicant').get(pk=pk)
        return _process_claimed(detail, lease_until)
    except Exception as error:
        logger.exception(
            'article_generation_error',
            extra={'event_type': 'article_generation_error', 'event_detail_id': pk},
        )
        return _record_unexpected_failure(pk, lease_until, error)


def _process_claimed(detail: EventDetail, lease_until: datetime) -> ArticleGenerationResult:
    """取った 1 件を生成し、結果を記録する。"""
    attempts = detail.article_generation_attempts
    skip_reason = _skip_reason(detail)
    if skip_reason:
        _release(detail.pk, lease_until, reset_attempts=True)
        outcome = SKIPPED_MANUAL if skip_reason == 'manual_edit' else SKIPPED
        return _log_result(detail.pk, attempts, outcome, skip_reason)
    if attempts > MAX_ATTEMPTS:
        # 処理中にプロセスが落ち続ける等で、締切切れの拾い直しが上限を超えた
        _release(detail.pk, lease_until, error='max_attempts')
        return _log_result(detail.pk, attempts, FAILED, 'max_attempts', gave_up=True)

    try:
        blog_output, sources = _generate(detail)
    except ArticleGenerationError as error:
        return _schedule_retry(detail.pk, attempts, lease_until, error)
    except Exception as error:
        logger.exception(
            'article_generation_error',
            extra={'event_type': 'article_generation_error', 'event_detail_id': detail.pk},
        )
        return _schedule_retry(detail.pk, attempts, lease_until, _unexpected(error))

    outcome, reason = _store_article(detail.pk, lease_until, blog_output, sources)
    if outcome == GENERATED:
        _after_generated(detail.pk)
    return _log_result(detail.pk, attempts, outcome, reason)


def _skip_reason(detail: EventDetail) -> str:
    """生成しない理由。生成してよければ空文字。"""
    if not detail.can_auto_generate_article:
        return 'not_eligible'
    state = detail.article_state()
    if state == EventDetail.ArticleState.MANUAL:
        return 'manual_edit'
    recorded_sources = (detail.article_source_video_id, detail.article_source_slide_name)
    if state == EventDetail.ArticleState.AUTO and detail.article_sources() == recorded_sources:
        # 今ある入力はすべて使って作ってある
        return 'unchanged'
    return ''


def _generate(detail: EventDetail) -> tuple[BlogOutput, BlogSources]:
    """その時点で揃っている字幕と PDF をすべて使って記事を作る。

    字幕を先に取り、動画があるのに字幕がまだ無い時は、PDF を読まずに上限の回まで待つ
    （最後の回は取れた入力だけで作る）。
    """
    transcript = fetch_transcript(detail)
    if detail.video_id and not transcript and detail.article_generation_attempts < MAX_ATTEMPTS:
        raise ArticleGenerationDeferred('waiting_for_transcript')
    sources = collect_blog_sources(detail)
    if not sources.has_text:
        # 文字の無い PDF など。入力無しでは記事を作らない
        raise ArticleGenerationError('no_source_text')
    blog_output = generate_blog(detail, model=settings.GEMINI_MODEL, sources=sources)
    if not blog_output.title:
        raise ArticleGenerationError('empty_output')
    return blog_output, sources


def _store_article(pk: int, lease_until: datetime, blog_output: BlogOutput,
                   sources: BlogSources) -> tuple[str, str]:
    """生成した記事を書き込む。処理中に編集・同意の変更・新しい入力・論理削除があれば書かない。"""
    with transaction.atomic():
        current = EventDetail.all_objects.select_for_update().get(pk=pk)
        if current.article_generation_requested_at != lease_until:
            # 処理中に新しい入力が来た（次の呼び出しで作り直す）か、手動の生成で印が外れた
            return SKIPPED, 'superseded'
        if not current.can_auto_generate_article:
            _release(pk, lease_until, reset_attempts=True)
            return SKIPPED, 'not_eligible'
        if current.article_state() == EventDetail.ArticleState.MANUAL:
            _release(pk, lease_until, reset_attempts=True)
            return SKIPPED_MANUAL, 'manual_edit'
        current.save(update_fields=set_generated_article(current, blog_output, sources.used_sources))
    return GENERATED, ''


def _after_generated(pk: int) -> None:
    """書き込みが確定した後に、PDF のサムネイルを作り、最初の 1 回だけ発表者に知らせる。

    サムネイルはストレージに書くので、結果がスキップになった時に孤児にならないようここで作る。
    ここでの失敗は記事の保存を覆さない（ログに残して続ける）。
    """
    try:
        detail = EventDetail.all_objects.select_related('event__community', 'applicant').get(pk=pk)
    except Exception:
        logger.exception(
            'article_after_generation_failed',
            extra={'event_type': 'article_after_generation_failed', 'event_detail_id': pk},
        )
        return
    try:
        if not detail.thumbnail_image:
            ensure_pdf_thumbnail(detail, save=True)
    except Exception:
        logger.exception(
            'article_thumbnail_failed',
            extra={'event_type': 'article_thumbnail_failed', 'event_detail_id': pk},
        )
    _notify_first_time(detail)


def _notify_first_time(detail: EventDetail) -> None:
    """発表者（Vket 由来の発表は申し込んだ人）に、最初の 1 回だけ記事の公開を知らせる。

    宛先が無い時は通知日時を入れない（後で宛先ができた時に知らせられるように）。
    先に通知日時を入れた 1 件だけが送る（作り直しや重なった呼び出しでは送らない）。
    """
    try:
        recipient = get_material_reminder_recipient(detail)
        if recipient is None or not recipient.email:
            logger.warning(
                'article_published_notification_skipped',
                extra={'event_type': 'article_published_notification_skipped', 'event_detail_id': detail.pk},
            )
            return
        first_time = EventDetail.all_objects.filter(
            pk=detail.pk, article_published_notified_at__isnull=True,
        ).update(article_published_notified_at=timezone.now())
        if first_time:
            notify_applicant_of_article_published(detail, recipient)
    except Exception:
        logger.exception(
            'article_published_notification_failed',
            extra={'event_type': 'article_published_notification_failed', 'event_detail_id': detail.pk},
        )


def _unexpected(error: Exception) -> ArticleGenerationError:
    """予期しない例外を、記録用の短い識別子を持つ失敗に変える（メッセージは記録しない）。"""
    return ArticleGenerationError(f'error:{type(error).__name__}')


def _record_unexpected_failure(pk: int, lease_until: datetime, error: Exception) -> ArticleGenerationResult:
    """処理の途中で例外が出た 1 件を失敗として記録する。DB 自体が使えない時は締切切れで拾い直される。"""
    try:
        attempts = (
            EventDetail.all_objects.filter(pk=pk)
            .values_list('article_generation_attempts', flat=True)
            .first()
        ) or 0
        return _schedule_retry(pk, attempts, lease_until, _unexpected(error))
    except Exception:
        logger.exception(
            'article_generation_failure_not_recorded',
            extra={'event_type': 'article_generation_failure_not_recorded', 'event_detail_id': pk},
        )
        return _log_result(pk, 0, FAILED, _unexpected(error).reason)


def _schedule_retry(pk: int, attempts: int, lease_until: datetime,
                    error: ArticleGenerationError) -> ArticleGenerationResult:
    """失敗・字幕待ちを記録し、上限までは間を空けて再試行する。上限に達したら印を外す。"""
    gave_up = attempts >= MAX_ATTEMPTS
    retry_at = None if gave_up else timezone.now() + RETRY_BASE_DELAY * (2 ** max(attempts - 1, 0))
    EventDetail.all_objects.filter(pk=pk, article_generation_requested_at=lease_until).update(
        article_generation_requested_at=retry_at,
        article_generation_last_error=error.reason[:LAST_ERROR_MAX_LENGTH],
    )
    outcome = DEFERRED if isinstance(error, ArticleGenerationDeferred) else FAILED
    return _log_result(pk, attempts, outcome, error.reason, gave_up=gave_up)


def _release(pk: int, lease_until: datetime, *, error: str = '', reset_attempts: bool = False) -> None:
    """生成待ちの印を外す。処理中の締切が自分の入れたものの時だけ（新しい印は残す）。"""
    fields: dict = {'article_generation_requested_at': None}
    if error:
        fields['article_generation_last_error'] = error
    if reset_attempts:
        fields['article_generation_attempts'] = 0
    EventDetail.all_objects.filter(pk=pk, article_generation_requested_at=lease_until).update(**fields)


def _log_result(pk: int, attempts: int, outcome: str, reason: str = '', *,
                gave_up: bool = False) -> ArticleGenerationResult:
    """1 件の結果を構造化ログに出して返す。"""
    result = ArticleGenerationResult(
        event_detail_id=pk,
        outcome=outcome,
        reason=reason,
        attempts=attempts,
        gave_up=gave_up,
    )
    log = logger.warning if outcome == FAILED else logger.info
    log('article_generation_result', extra={'event_type': 'article_generation_result', **asdict(result)})
    return result
