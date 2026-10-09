"""送信日時を過ぎた予約を Discord の告知チャンネルへ送る（Cloud Scheduler から 1 分ごと）。

二重送信を防ぐため、1 件ずつ次の順で処理する。
1. トランザクションの中で送信できる予約を 1 件選び（select_for_update(skip_locked=True)）、
   条件付き UPDATE でリースを付ける。リース付きの行は、別の実行が選ぶ対象から外れる
2. トランザクションの外で 1 回だけ送る（Discord の応答を待つ間、行ロックを持たない）
3. 自分のリースが残っている時だけ結果を書く。リースを失っていたら行は書き換えない

自動で送り直すのは、本文が Discord に届いていないと言える失敗（429・接続前の失敗）だけ。
届いたかどうか分からなくなった予約（5xx、応答待ちのタイムアウト、リースの期限切れ、送信中の予期しない例外）は
自動で送り直さず、失敗にしてスタッフの「再送する」に任せる。

読み込み・送信・結果の記録は、それぞれ別に例外を捕まえる。送れたのに記録できなかった時は、行を書き換えずに
Discord のメッセージ ID を構造化ログに残し、sent_unrecorded として数える。

1 回の呼び出しで送るのは MAX_MESSAGES_PER_RUN 件まで。skipped は、この回で送らなかった送信日時を過ぎた予約の数
（別の実行に先を越された行と、上限を超えて次の回へ回した分）。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .discord_client import MAY_HAVE_ARRIVED_GUIDE, SendResult, send_announcement
from .models import LAST_ERROR_MAX_LENGTH, DiscordScheduledMessage

logger = logging.getLogger(__name__)

Status = DiscordScheduledMessage.Status

# 送信を試みる回数の上限（初回を含む）
MAX_SEND_ATTEMPTS = 3
# n 回目の送信に失敗した後、次に試すまでの待ち時間
RETRY_DELAYS = (timedelta(minutes=2), timedelta(minutes=5))
# 1 件の送信処理が終わるまでのリース。1 回の送信は接続 5 秒 + 応答 10 秒で打ち切る
LEASE_DURATION = timedelta(minutes=5)
# 1 回の呼び出しで送る件数の上限（前段の nginx / uWSGI の 120 秒以内に収める）
MAX_MESSAGES_PER_RUN = 5

OUTCOME_SENT = 'sent'
OUTCOME_SENT_UNRECORDED = 'sent_unrecorded'
OUTCOME_RETRYING = 'retrying'
OUTCOME_FAILED = 'failed'
OUTCOME_SKIPPED = 'skipped'
OUTCOMES = (OUTCOME_SENT, OUTCOME_SENT_UNRECORDED, OUTCOME_RETRYING, OUTCOME_FAILED, OUTCOME_SKIPPED)

ABANDONED_LEASE_ERROR = f'送信処理が途中で止まりました。{MAY_HAVE_ARRIVED_GUIDE}'


@dataclass
class DeliverySummary:
    """1 回の呼び出しの結果。件数はエンドポイントの応答と構造化ログに出す。"""

    results: list[dict] = field(default_factory=list)
    # 上限を超えて次の回へ回した、送信日時を過ぎた予約の数（skipped に含めて数える）
    deferred: int = 0

    def add(self, message_id: int, outcome: str, **detail) -> None:
        self.results.append({'id': message_id, 'outcome': outcome, **detail})

    def counts(self) -> dict[str, int]:
        counts = {
            outcome: sum(1 for result in self.results if result['outcome'] == outcome) for outcome in OUTCOMES
        }
        counts[OUTCOME_SKIPPED] += self.deferred
        return counts

    def as_dict(self) -> dict:
        return {**self.counts(), 'results': self.results}


def process_due_messages(*, now: datetime | None = None) -> dict:
    """送信日時を過ぎた予約中のメッセージを送り、件数と 1 件ごとの結果を返す。"""
    current = now or timezone.now()
    summary = DeliverySummary()
    _fail_abandoned_leases(current, summary)
    lost_ids: set[int] = set()
    delivered = 0
    while delivered < MAX_MESSAGES_PER_RUN:
        claim = _claim_next_due(current, lost_ids)
        if claim is None:
            break
        message_id, lease_token = claim
        if lease_token is None:
            # 別の実行に先を越された。この回ではこの行を候補から外し、次の候補へ進む
            lost_ids.add(message_id)
            summary.add(message_id, OUTCOME_SKIPPED, reason='claimed_by_another_run')
            continue
        _deliver_claimed(message_id, lease_token, current, summary)
        delivered += 1
    else:
        # 上限まで送った。残りは次の回で送る
        summary.deferred = DiscordScheduledMessage.objects.due(current).exclude(pk__in=lost_ids).count()
    _log_summary(summary)
    return summary.as_dict()


def _fail_abandoned_leases(now: datetime, summary: DeliverySummary) -> None:
    """リースの期限が切れた予約（送信処理が途中で止まった）を、送り直さずにまとめて失敗にする。"""
    abandoned = DiscordScheduledMessage.objects.filter(
        status=Status.SCHEDULED, lease_expires_at__isnull=False, lease_expires_at__lte=now,
    )
    with transaction.atomic():
        message_ids = list(abandoned.select_for_update(skip_locked=True).values_list('pk', flat=True))
        if not message_ids:
            return
        abandoned.filter(pk__in=message_ids).update(
            status=Status.FAILED,
            last_error=ABANDONED_LEASE_ERROR,
            next_attempt_at=None,
            lease_token='',
            lease_expires_at=None,
            updated_at=now,
        )
    for message_id in message_ids:
        summary.add(message_id, OUTCOME_FAILED, reason='lease_expired')
    logger.warning(
        'Discord scheduled message leases expired: count=%s ids=%s', len(message_ids), message_ids,
        extra={
            'delivery_outcome': 'lease_expired',
            'expired_lease_count': len(message_ids),
            'expired_lease_ids': message_ids,
        },
    )


def _claim_next_due(now: datetime, exclude_ids: set[int]) -> tuple[int, str | None] | None:
    """送信できる予約を 1 件選んでリースを付ける。

    選べる予約が無ければ None、取れたら (id, token)、別の実行に先を越されたら (id, None) を返す。
    """
    candidates = DiscordScheduledMessage.objects.due(now)
    if exclude_ids:
        candidates = candidates.exclude(pk__in=exclude_ids)
    with transaction.atomic():
        message_id = (
            candidates.select_for_update(skip_locked=True)
            .order_by('scheduled_at', 'pk')
            .values_list('pk', flat=True)
            .first()
        )
        if message_id is None:
            return None
        lease_token = uuid.uuid4().hex
        # SKIP LOCKED で選んだ行はこのトランザクションが握っているので、通常は必ず更新できる。
        # 行ロックが効かない DB でも二重に取らないよう、リースが空いている時だけ更新する
        claimed = DiscordScheduledMessage.objects.due(now).filter(pk=message_id).update(
            lease_token=lease_token,
            lease_expires_at=now + LEASE_DURATION,
            attempt_count=F('attempt_count') + 1,
            updated_at=now,
        )
    return message_id, (lease_token if claimed else None)


def _deliver_claimed(message_id: int, lease_token: str, now: datetime, summary: DeliverySummary) -> None:
    """取った 1 件を送る。読み込み・送信・結果の記録で別々に例外を捕まえ、残りの予約とまとめのログを止めない。"""
    try:
        message = DiscordScheduledMessage.objects.get(pk=message_id)
    except Exception as error:
        _recover_before_sending(message_id, lease_token, error, now, summary)
        return
    try:
        result = send_announcement(message.body, mention_everyone=message.mention_everyone_confirmed)
    except Exception as error:
        _recover_while_sending(message_id, lease_token, error, now, summary)
        return
    try:
        _record_result(message, lease_token, result, now, summary)
    except Exception as error:
        _recover_from_record_error(message, lease_token, result, error, now, summary)


def _record_result(
    message: DiscordScheduledMessage, lease_token: str, result: SendResult, now: datetime, summary: DeliverySummary,
) -> None:
    if result.ok:
        _record_sent(message.pk, lease_token, result.message_id, summary)
    elif result.retryable and message.attempt_count < MAX_SEND_ATTEMPTS:
        _record_retry(message, lease_token, result, now, summary)
    else:
        _record_failure(message, lease_token, result, now, summary)


def _record_sent(message_id: int, lease_token: str, discord_message_id: str, summary: DeliverySummary) -> None:
    sent_at = timezone.now()
    recorded = DiscordScheduledMessage.objects.filter(pk=message_id, lease_token=lease_token).update(
        status=Status.SENT,
        sent_at=sent_at,
        discord_message_id=discord_message_id,
        last_error='',
        next_attempt_at=None,
        lease_token='',
        lease_expires_at=None,
        updated_at=sent_at,
    )
    if not recorded:
        # リースを失っていた（期限切れで失敗として記録済みなど）。行は書き換えず、送れた事実をログに残す
        _report_sent_unrecorded(message_id, discord_message_id, summary, reason='lease_lost')
        return
    logger.info(
        'Discord scheduled message sent: id=%s discord_message_id=%s', message_id, discord_message_id,
        extra={'scheduled_message_id': message_id, 'delivery_outcome': OUTCOME_SENT},
    )
    summary.add(message_id, OUTCOME_SENT, discord_message_id=discord_message_id)


def _record_retry(
    message: DiscordScheduledMessage, lease_token: str, result: SendResult, now: datetime, summary: DeliverySummary,
) -> None:
    delay = RETRY_DELAYS[min(message.attempt_count, len(RETRY_DELAYS)) - 1]
    if not _release_for_retry(message.pk, lease_token, result.error, now + delay, now):
        _report_lease_lost(message, result, summary)
        return
    logger.warning(
        'Discord scheduled message will be retried: id=%s error=%s', message.pk, result.error,
        extra={'scheduled_message_id': message.pk, 'delivery_outcome': OUTCOME_RETRYING},
    )
    summary.add(message.pk, OUTCOME_RETRYING, attempt=message.attempt_count, error=result.error)


def _record_failure(
    message: DiscordScheduledMessage, lease_token: str, result: SendResult, now: datetime, summary: DeliverySummary,
) -> None:
    if not _fail_with_error(message.pk, lease_token, result.error, now):
        _report_lease_lost(message, result, summary)
        return
    logger.error(
        'Discord scheduled message failed: id=%s error=%s', message.pk, result.error,
        extra={'scheduled_message_id': message.pk, 'delivery_outcome': OUTCOME_FAILED},
    )
    summary.add(message.pk, OUTCOME_FAILED, attempt=message.attempt_count, error=result.error)


def _report_lease_lost(message: DiscordScheduledMessage, result: SendResult, summary: DeliverySummary) -> None:
    """結果を書く前にリースを失っていた。期限切れの処理が失敗として記録しているので、行は書き換えない。"""
    logger.warning(
        'Discord scheduled message lost its lease before the result was recorded: id=%s error=%s',
        message.pk, result.error,
        extra={'scheduled_message_id': message.pk, 'delivery_outcome': OUTCOME_FAILED, 'failure_reason': 'lease_lost'},
    )
    summary.add(message.pk, OUTCOME_FAILED, reason='lease_lost', attempt=message.attempt_count, error=result.error)


def _report_sent_unrecorded(
    message_id: int, discord_message_id: str, summary: DeliverySummary, *, reason: str, error_type: str = '',
) -> None:
    """送れたのに記録できなかった。二重に送らないよう、Discord のメッセージ ID をログに残す。"""
    logger.error(
        'Discord scheduled message was sent but could not be recorded: id=%s discord_message_id=%s reason=%s',
        message_id, discord_message_id, reason,
        extra={
            'scheduled_message_id': message_id,
            'discord_message_id': discord_message_id,
            'delivery_outcome': OUTCOME_SENT_UNRECORDED,
            'unrecorded_reason': reason,
            'error_type': error_type,
        },
    )
    detail = {'error_type': error_type} if error_type else {}
    summary.add(
        message_id, OUTCOME_SENT_UNRECORDED, discord_message_id=discord_message_id, reason=reason, **detail,
    )


def _recover_before_sending(
    message_id: int, lease_token: str, error: Exception, now: datetime, summary: DeliverySummary,
) -> None:
    """送る前（行の読み込み）で止まった。本文は届いていないので、試行回数が残っていれば再試行待ちに戻す。"""
    error_type = type(error).__name__
    error_text = f'送る前に予期しないエラーが起きました（{error_type}）。'
    try:
        outcome = _release_or_fail(message_id, lease_token, error_text, now)
    except Exception:
        # 書けない時は、リースの期限が切れた後の回で失敗として記録される
        outcome = OUTCOME_FAILED
    _report_unexpected_error(message_id, error_type, 'before_sending', outcome, summary)


def _recover_while_sending(
    message_id: int, lease_token: str, error: Exception, now: datetime, summary: DeliverySummary,
) -> None:
    """送信の途中で止まった。届いたかどうか分からないので、送り直さずに失敗にする。"""
    error_type = type(error).__name__
    error_text = f'送信中に予期しないエラーが起きました（{error_type}）。{MAY_HAVE_ARRIVED_GUIDE}'
    _fail_quietly(message_id, lease_token, error_text, now)
    _report_unexpected_error(message_id, error_type, 'while_sending', OUTCOME_FAILED, summary)


def _recover_from_record_error(
    message: DiscordScheduledMessage, lease_token: str, result: SendResult, error: Exception, now: datetime,
    summary: DeliverySummary,
) -> None:
    """送信の結果を記録する途中で止まった。送れていたら sent_unrecorded、送れていなければその結果のまま失敗にする。"""
    error_type = type(error).__name__
    if result.ok:
        _report_sent_unrecorded(message.pk, result.message_id, summary, reason='record_failed', error_type=error_type)
        return
    # 「届いている可能性」の案内は、結果そのもの（5xx など）が言っている時だけ付く
    error_text = f'{result.error}結果を記録する途中で予期しないエラーが起きました（{error_type}）。'
    if result.retryable:
        error_text += 'Discord には届いていないので、「再送する」で送れます。'
    _fail_quietly(message.pk, lease_token, error_text, now)
    _report_unexpected_error(message.pk, error_type, 'recording', OUTCOME_FAILED, summary)


def _report_unexpected_error(
    message_id: int, error_type: str, stage: str, outcome: str, summary: DeliverySummary,
) -> None:
    # 例外の文字列は webhook の URL を含みうるので、型の名前だけを残す（トレースバックも出さない）
    logger.error(
        'Discord scheduled message raised an unexpected error: id=%s stage=%s error_type=%s',
        message_id, stage, error_type,
        extra={
            'scheduled_message_id': message_id,
            'delivery_outcome': outcome,
            'error_stage': stage,
            'error_type': error_type,
        },
    )
    summary.add(message_id, outcome, reason='unexpected_error', stage=stage, error_type=error_type)


def _release_or_fail(message_id: int, lease_token: str, error_text: str, now: datetime) -> str:
    """届いていない失敗を、試行回数が残っていれば再試行待ちに戻し、残っていなければ失敗にする。"""
    if _release_for_retry(message_id, lease_token, error_text, now + RETRY_DELAYS[0], now):
        return OUTCOME_RETRYING
    _fail_with_error(message_id, lease_token, error_text, now)
    return OUTCOME_FAILED


def _release_for_retry(
    message_id: int, lease_token: str, error_text: str, next_attempt_at: datetime, now: datetime,
) -> bool:
    """リースを外して再試行待ちに戻す（自分のリースで、試行回数が上限に達していない時だけ）。"""
    released = DiscordScheduledMessage.objects.filter(
        pk=message_id, lease_token=lease_token, attempt_count__lt=MAX_SEND_ATTEMPTS,
    ).update(
        last_error=error_text[:LAST_ERROR_MAX_LENGTH],
        next_attempt_at=next_attempt_at,
        lease_token='',
        lease_expires_at=None,
        updated_at=now,
    )
    return bool(released)


def _fail_with_error(message_id: int, lease_token: str, error_text: str, now: datetime) -> bool:
    """失敗にする（自分のリースが残っている時だけ）。書けたかを返す。"""
    failed = DiscordScheduledMessage.objects.filter(pk=message_id, lease_token=lease_token).update(
        status=Status.FAILED,
        last_error=error_text[:LAST_ERROR_MAX_LENGTH],
        next_attempt_at=None,
        lease_token='',
        lease_expires_at=None,
        updated_at=now,
    )
    return bool(failed)


def _fail_quietly(message_id: int, lease_token: str, error_text: str, now: datetime) -> None:
    """例外の後始末で失敗にする。ここでも書けなければ、リースの期限が切れた後の回で失敗として記録される。"""
    try:
        _fail_with_error(message_id, lease_token, error_text, now)
    except Exception as error:
        logger.error(
            'Discord scheduled message could not be marked as failed: id=%s error_type=%s',
            message_id, type(error).__name__,
            extra={'scheduled_message_id': message_id, 'error_type': type(error).__name__},
        )


def _log_summary(summary: DeliverySummary) -> None:
    if not summary.results and not summary.deferred:
        return
    counts = summary.counts()
    logger.info(
        'Discord scheduled messages processed: sent=%s sent_unrecorded=%s retrying=%s failed=%s skipped=%s',
        counts[OUTCOME_SENT], counts[OUTCOME_SENT_UNRECORDED], counts[OUTCOME_RETRYING],
        counts[OUTCOME_FAILED], counts[OUTCOME_SKIPPED],
        extra={f'delivery_{outcome}_count': count for outcome, count in counts.items()},
    )
