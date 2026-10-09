"""送信日時を過ぎた予約を Discord の告知チャンネルへ送る（Cloud Scheduler から 1 分ごと）。

二重送信を防ぐため、1 件ずつ次の順で処理する。
1. トランザクションの中で送信できる予約を 1 件選び（select_for_update(skip_locked=True)）、
   条件付き UPDATE でリースを付ける。リース付きの行は、別の実行が選ぶ対象から外れる
2. トランザクションの外で 1 回だけ送る（Discord の応答を待つ間、行ロックを持たない）
3. 自分のリースが残っている時だけ結果を書く

自動で送り直すのは、本文が Discord に届いていないと言える失敗（429・接続前の失敗）だけ。
届いたかどうか分からなくなった予約（5xx、応答待ちのタイムアウト、リースの期限切れ、送信中の予期しない例外）は
自動で送り直さず、失敗にしてスタッフの「再送する」に任せる。

1 回の呼び出しで送るのは MAX_MESSAGES_PER_RUN 件まで。送信日時を過ぎていても上限を超えた分は
送らずに次の回へ回し、その件数を skipped として数える。
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
OUTCOME_RETRYING = 'retrying'
OUTCOME_FAILED = 'failed'
OUTCOME_SKIPPED = 'skipped'

ABANDONED_LEASE_ERROR = f'送信処理が途中で止まりました。{MAY_HAVE_ARRIVED_GUIDE}'


@dataclass
class DeliverySummary:
    """1 回の呼び出しの結果。件数はエンドポイントの応答と構造化ログに出す。"""

    results: list[dict] = field(default_factory=list)
    # 送信日時を過ぎているが、1 回の上限を超えたため次の回へ回した件数
    skipped: int = 0

    def add(self, message_id: int, outcome: str, **detail) -> None:
        self.results.append({'id': message_id, 'outcome': outcome, **detail})

    def counts(self) -> dict[str, int]:
        counts = {
            outcome: sum(1 for result in self.results if result['outcome'] == outcome)
            for outcome in (OUTCOME_SENT, OUTCOME_RETRYING, OUTCOME_FAILED)
        }
        counts[OUTCOME_SKIPPED] = self.skipped
        return counts

    def as_dict(self) -> dict:
        return {**self.counts(), 'results': self.results}


def process_due_messages(*, now: datetime | None = None) -> dict:
    """送信日時を過ぎた予約中のメッセージを送り、件数と 1 件ごとの結果を返す。"""
    current = now or timezone.now()
    summary = DeliverySummary()
    _fail_abandoned_leases(current, summary)
    for _ in range(MAX_MESSAGES_PER_RUN):
        claim = _claim_next_due(current)
        if claim is None:
            break
        _deliver_claimed(*claim, current, summary)
    else:
        # 上限まで送った。残りは次の回で送る
        summary.skipped = DiscordScheduledMessage.objects.due(current).count()
    _log_summary(summary)
    return summary.as_dict()


def _fail_abandoned_leases(now: datetime, summary: DeliverySummary) -> None:
    """リースの期限が切れた予約（送信処理が途中で止まった）を、送り直さずに失敗にする。"""
    abandoned = DiscordScheduledMessage.objects.filter(
        status=Status.SCHEDULED, lease_expires_at__isnull=False, lease_expires_at__lte=now,
    )
    for message_id in list(abandoned.values_list('pk', flat=True)):
        updated = abandoned.filter(pk=message_id).update(
            status=Status.FAILED,
            last_error=ABANDONED_LEASE_ERROR,
            next_attempt_at=None,
            lease_token='',
            lease_expires_at=None,
            updated_at=now,
        )
        if updated:
            summary.add(message_id, OUTCOME_FAILED, reason='lease_expired')
            logger.warning(
                'Discord scheduled message lease expired: id=%s',
                message_id,
                extra={'scheduled_message_id': message_id, 'delivery_outcome': 'lease_expired'},
            )


def _claim_next_due(now: datetime) -> tuple[int, str] | None:
    """送信できる予約を 1 件選んでリースを付け、(id, token) を返す。取れなければ None。"""
    with transaction.atomic():
        message_id = (
            DiscordScheduledMessage.objects.due(now)
            .select_for_update(skip_locked=True)
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
    # 先を越された時は、同じ行を取り直し続けないよう、この回はここで終える
    return (message_id, lease_token) if claimed else None


def _deliver_claimed(message_id: int, lease_token: str, now: datetime, summary: DeliverySummary) -> None:
    """取った 1 件を送る。予期しない例外は 1 件ずつ記録し、残りの予約とまとめのログを止めない。"""
    try:
        message = DiscordScheduledMessage.objects.get(pk=message_id)
    except Exception as error:
        # 送る前に止まった（本文は Discord に届いていない）ので、再試行待ちに戻す
        _recover_from_unexpected_error(message_id, lease_token, error, now, summary, before_sending=True)
        return
    try:
        result = send_announcement(message.body, mention_everyone=message.mention_everyone_confirmed)
        _record_result(message, lease_token, result, now, summary)
    except Exception as error:
        # 送った後かもしれない（届いたかどうか分からない）ので、送り直さずに失敗にする
        _recover_from_unexpected_error(message_id, lease_token, error, now, summary, before_sending=False)


def _record_result(
    message: DiscordScheduledMessage, lease_token: str, result: SendResult, now: datetime, summary: DeliverySummary,
) -> None:
    message_id = message.pk
    log_extra = {'scheduled_message_id': message_id}
    if result.ok:
        _mark_sent(message_id, lease_token, result.message_id)
        summary.add(message_id, OUTCOME_SENT, discord_message_id=result.message_id)
        return
    if result.retryable and message.attempt_count < MAX_SEND_ATTEMPTS:
        next_attempt_at = now + RETRY_DELAYS[min(message.attempt_count, len(RETRY_DELAYS)) - 1]
        _release_for_retry(message_id, lease_token, result.error, next_attempt_at, now)
        logger.warning(
            'Discord scheduled message will be retried: id=%s error=%s', message_id, result.error,
            extra={**log_extra, 'delivery_outcome': OUTCOME_RETRYING},
        )
        summary.add(message_id, OUTCOME_RETRYING, attempt=message.attempt_count, error=result.error)
        return
    _fail_with_error(message_id, lease_token, result.error, now)
    logger.error(
        'Discord scheduled message failed: id=%s error=%s', message_id, result.error,
        extra={**log_extra, 'delivery_outcome': OUTCOME_FAILED},
    )
    summary.add(message_id, OUTCOME_FAILED, attempt=message.attempt_count, error=result.error)


def _recover_from_unexpected_error(
    message_id: int, lease_token: str, error: Exception, now: datetime, summary: DeliverySummary, *,
    before_sending: bool,
) -> None:
    # 例外の文字列は webhook の URL を含みうるので、型の名前だけを残す（トレースバックも出さない）
    error_type = type(error).__name__
    try:
        if before_sending:
            error_text = f'送る前に予期しないエラーが起きました（{error_type}）。'
            outcome = _release_or_fail(message_id, lease_token, error_text, now)
        else:
            error_text = f'送信中に予期しないエラーが起きました（{error_type}）。{MAY_HAVE_ARRIVED_GUIDE}'
            _fail_with_error(message_id, lease_token, error_text, now)
            outcome = OUTCOME_FAILED
    except Exception:
        # 結果も書けない（DB に書けないなど）。リースの期限が切れた後の回で、失敗として記録される
        outcome = OUTCOME_FAILED
    summary.add(message_id, outcome, reason='unexpected_error', error_type=error_type)
    logger.error(
        'Discord scheduled message raised an unexpected error: id=%s error_type=%s before_sending=%s',
        message_id, error_type, before_sending,
        extra={'scheduled_message_id': message_id, 'delivery_outcome': outcome, 'error_type': error_type},
    )


def _release_or_fail(message_id: int, lease_token: str, error_text: str, now: datetime) -> str:
    """送る前の失敗を、試行回数が残っていれば再試行待ちに戻し、残っていなければ失敗にする。"""
    if _release_for_retry(message_id, lease_token, error_text, now + RETRY_DELAYS[0], now):
        return OUTCOME_RETRYING
    _fail_with_error(message_id, lease_token, error_text, now)
    return OUTCOME_FAILED


def _mark_sent(message_id: int, lease_token: str, discord_message_id: str) -> None:
    sent_at = timezone.now()
    fields = {
        'status': Status.SENT,
        'sent_at': sent_at,
        'discord_message_id': discord_message_id,
        'last_error': '',
        'next_attempt_at': None,
        'lease_token': '',
        'lease_expires_at': None,
        'updated_at': sent_at,
    }
    updated = DiscordScheduledMessage.objects.filter(pk=message_id, lease_token=lease_token).update(**fields)
    if not updated:
        # リースを失っていても Discord は受け付けている。再送で二重にならないよう送信済みを記録する
        logger.error(
            'Discord scheduled message sent after its lease was lost: id=%s', message_id,
            extra={'scheduled_message_id': message_id, 'delivery_outcome': 'sent_without_lease'},
        )
        DiscordScheduledMessage.objects.filter(pk=message_id).update(**fields)
    logger.info(
        'Discord scheduled message sent: id=%s discord_message_id=%s', message_id, discord_message_id,
        extra={'scheduled_message_id': message_id, 'delivery_outcome': OUTCOME_SENT},
    )


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


def _fail_with_error(message_id: int, lease_token: str, error_text: str, now: datetime) -> None:
    DiscordScheduledMessage.objects.filter(pk=message_id, lease_token=lease_token).update(
        status=Status.FAILED,
        last_error=error_text[:LAST_ERROR_MAX_LENGTH],
        next_attempt_at=None,
        lease_token='',
        lease_expires_at=None,
        updated_at=now,
    )


def _log_summary(summary: DeliverySummary) -> None:
    if not summary.results and not summary.skipped:
        return
    counts = summary.counts()
    logger.info(
        'Discord scheduled messages processed: sent=%s retrying=%s failed=%s skipped=%s',
        counts[OUTCOME_SENT], counts[OUTCOME_RETRYING], counts[OUTCOME_FAILED], counts[OUTCOME_SKIPPED],
        extra={f'delivery_{outcome}_count': count for outcome, count in counts.items()},
    )
