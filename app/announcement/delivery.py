"""送信日時を過ぎた予約を Discord の告知チャンネルへ送る（Cloud Scheduler から 1 分ごと）。

二重送信を防ぐため、1 件ずつ次の順で処理する。
1. トランザクションの中で送信できる予約を 1 件選び（select_for_update(skip_locked=True)）、
   リースが空いている時だけ条件付き UPDATE でリースを付ける。別の実行は、リース付きの行を選ばない
2. トランザクションの外で 1 回だけ送る（Discord の応答を待つ間、行ロックを持たない）
3. 自分のリースが残っている時だけ結果を書く

送ったかどうか分からなくなった予約（リースの期限切れ、応答待ちのタイムアウト）は自動で送り直さず、
失敗にしてスタッフの「再送する」に任せる。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .discord_client import SendResult, send_announcement
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
OUTCOMES = (OUTCOME_SENT, OUTCOME_RETRYING, OUTCOME_FAILED, OUTCOME_SKIPPED)

ABANDONED_LEASE_ERROR = (
    '送信処理が途中で止まりました。届いている可能性があるため、チャンネルを確認してから再送してください。'
)


@dataclass
class DeliverySummary:
    """1 回の呼び出しの結果。件数はエンドポイントの応答と構造化ログに出す。"""

    results: list[dict] = field(default_factory=list)

    def add(self, message_id: int, outcome: str, **detail) -> None:
        self.results.append({'id': message_id, 'outcome': outcome, **detail})

    def counts(self) -> dict[str, int]:
        return {
            outcome: sum(1 for result in self.results if result['outcome'] == outcome)
            for outcome in OUTCOMES
        }

    def as_dict(self) -> dict:
        return {**self.counts(), 'results': self.results}


def process_due_messages(*, now: datetime | None = None) -> dict:
    """送信日時を過ぎた予約中のメッセージを送り、件数と 1 件ごとの結果を返す。"""
    current = now or timezone.now()
    summary = DeliverySummary()
    _fail_abandoned_leases(current, summary)
    for _ in range(MAX_MESSAGES_PER_RUN):
        message_id, lease_token = _claim_next_due(current)
        if message_id is None:
            break
        if lease_token is None:
            summary.add(message_id, OUTCOME_SKIPPED, reason='claimed_by_another_run')
            continue
        _deliver(message_id, lease_token, current, summary)
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


def _claim_next_due(now: datetime) -> tuple[int | None, str | None]:
    """送信できる予約を 1 件選んでリースを付ける。

    (id, token) を返す。選べる予約が無ければ (None, None)、別の実行に先を越されたら (id, None)。
    """
    with transaction.atomic():
        message_id = (
            DiscordScheduledMessage.objects.due(now)
            .select_for_update(skip_locked=True)
            .order_by('scheduled_at', 'pk')
            .values_list('pk', flat=True)
            .first()
        )
        if message_id is None:
            return None, None
        lease_token = uuid.uuid4().hex
        claimed = DiscordScheduledMessage.objects.due(now).filter(pk=message_id).update(
            lease_token=lease_token,
            lease_expires_at=now + LEASE_DURATION,
            attempt_count=F('attempt_count') + 1,
            updated_at=now,
        )
    return message_id, (lease_token if claimed else None)


def _deliver(message_id: int, lease_token: str, now: datetime, summary: DeliverySummary) -> None:
    message = DiscordScheduledMessage.objects.get(pk=message_id)
    result = send_announcement(message.body, mention_everyone=message.mention_everyone_confirmed)
    if result.ok:
        _mark_sent(message_id, lease_token, result.message_id)
        summary.add(message_id, OUTCOME_SENT, discord_message_id=result.message_id)
        return
    if result.retryable and message.attempt_count < MAX_SEND_ATTEMPTS:
        next_attempt_at = now + RETRY_DELAYS[min(message.attempt_count, len(RETRY_DELAYS)) - 1]
        _release_for_retry(message_id, lease_token, result, next_attempt_at, now)
        summary.add(message_id, OUTCOME_RETRYING, attempt=message.attempt_count, error=result.error)
        return
    _mark_failed(message_id, lease_token, result, now)
    summary.add(message_id, OUTCOME_FAILED, attempt=message.attempt_count, error=result.error)


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
    message_id: int, lease_token: str, result: SendResult, next_attempt_at: datetime, now: datetime,
) -> None:
    DiscordScheduledMessage.objects.filter(pk=message_id, lease_token=lease_token).update(
        last_error=result.error[:LAST_ERROR_MAX_LENGTH],
        next_attempt_at=next_attempt_at,
        lease_token='',
        lease_expires_at=None,
        updated_at=now,
    )
    logger.warning(
        'Discord scheduled message will be retried: id=%s error=%s', message_id, result.error,
        extra={'scheduled_message_id': message_id, 'delivery_outcome': OUTCOME_RETRYING},
    )


def _mark_failed(message_id: int, lease_token: str, result: SendResult, now: datetime) -> None:
    DiscordScheduledMessage.objects.filter(pk=message_id, lease_token=lease_token).update(
        status=Status.FAILED,
        last_error=result.error[:LAST_ERROR_MAX_LENGTH],
        next_attempt_at=None,
        lease_token='',
        lease_expires_at=None,
        updated_at=now,
    )
    logger.error(
        'Discord scheduled message failed: id=%s error=%s', message_id, result.error,
        extra={'scheduled_message_id': message_id, 'delivery_outcome': OUTCOME_FAILED},
    )


def _log_summary(summary: DeliverySummary) -> None:
    if not summary.results:
        return
    counts = summary.counts()
    logger.info(
        'Discord scheduled messages processed: sent=%s retrying=%s failed=%s skipped=%s',
        counts[OUTCOME_SENT], counts[OUTCOME_RETRYING], counts[OUTCOME_FAILED], counts[OUTCOME_SKIPPED],
        extra={f'delivery_{outcome}_count': count for outcome, count in counts.items()},
    )
