"""発表資料公開リマインドの送信処理。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q, QuerySet
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from event.material_upload_reminders import get_material_reminder_recipient
from event.models import EventDetail, MaterialUploadReminderLog
from website.constants import build_site_url

logger = logging.getLogger(__name__)

SLIDE_REMINDER_DELAY_DAYS = 7
DEFAULT_SLIDE_REMINDER_LIMIT = 50


@dataclass(frozen=True)
class SlideReminderResult:
    """資料公開リマインド処理の実行結果。"""

    dry_run: bool
    candidates: int
    sent: int
    skipped: int
    failed: int
    event_detail_ids: list[int]

    def as_dict(self) -> dict[str, object]:
        """JSONレスポンスや管理コマンド出力用のdictに変換する。"""
        return {
            "dryRun": self.dry_run,
            "candidates": self.candidates,
            "sent": self.sent,
            "skipped": self.skipped,
            "failed": self.failed,
            "eventDetailIds": self.event_detail_ids,
        }


def has_published_material(event_detail: EventDetail) -> bool:
    """発表資料または動画が公開済みかを返す。"""
    return bool(event_detail.slide_url or event_detail.slide_file or event_detail.youtube_url)


def get_slide_reminder_queryset(now=None) -> QuerySet[EventDetail]:
    """開催から1週間以上経った未公開LTのリマインド候補を返す。"""
    current = now or timezone.now()
    cutoff_date = timezone.localdate(current) - timedelta(days=SLIDE_REMINDER_DELAY_DAYS)
    return (
        EventDetail.objects.filter(
            detail_type='LT',
            status='approved',
            event__date__lte=cutoff_date,
            material_upload_reminder_log__status=MaterialUploadReminderLog.Status.SENT,
            material_upload_reminder_log__follow_up_sent_at__isnull=True,
        )
        .filter(Q(slide_url='') | Q(slide_url__isnull=True))
        .filter(Q(slide_file='') | Q(slide_file__isnull=True))
        .filter(Q(youtube_url='') | Q(youtube_url__isnull=True))
        .select_related('applicant', 'event', 'event__community', 'material_upload_reminder_log')
        .order_by('event__date', 'pk')
    )


def _build_application_edit_url(event_detail: EventDetail) -> str:
    path = reverse('account:lt_application_edit', kwargs={'pk': event_detail.pk})
    return build_site_url(path)


def send_slide_reminder_email(event_detail: EventDetail) -> bool:
    """資料未公開の発表者へリマインドメールを送信する。"""
    applicant = get_material_reminder_recipient(event_detail)
    if not applicant or not applicant.email:
        logger.warning("資料公開リマインド: 申請者またはメールアドレスがありません。EventDetail=%s", event_detail.pk)
        return False

    edit_url = _build_application_edit_url(event_detail)
    context = {
        'applicant': applicant,
        'community': event_detail.event.community,
        'event': event_detail.event,
        'event_detail': event_detail,
        'edit_url': edit_url,
    }
    subject = f"[{event_detail.event.community.name}] 発表資料公開のお願い"
    message = render_to_string('event/email/slide_publication_reminder.txt', context).strip()
    html_message = render_to_string('event/email/slide_publication_reminder.html', context)

    sent = send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[applicant.email],
        html_message=html_message,
    )
    if sent:
        logger.info("資料公開リマインド送信成功: EventDetail=%s, email=%s", event_detail.pk, applicant.email)
    else:
        logger.warning("資料公開リマインド送信失敗: EventDetail=%s, email=%s", event_detail.pk, applicant.email)
    return bool(sent)


def process_slide_publication_reminders(
    *,
    dry_run: bool = False,
    limit: int = DEFAULT_SLIDE_REMINDER_LIMIT,
    now=None,
) -> SlideReminderResult:
    """未公開資料の1週間後リマインドを送信する。"""
    queryset = get_slide_reminder_queryset(now=now)
    candidate_ids = list(queryset.values_list('id', flat=True)[:limit])
    if dry_run:
        return SlideReminderResult(
            dry_run=True,
            candidates=len(candidate_ids),
            sent=0,
            skipped=0,
            failed=0,
            event_detail_ids=candidate_ids,
        )

    sent = 0
    skipped = 0
    failed = 0
    processed_ids: list[int] = []

    for event_detail_id in candidate_ids:
        try:
            with transaction.atomic():
                locked = (
                    EventDetail.objects.select_for_update()
                    .select_related('applicant', 'event', 'event__community')
                    .select_related('material_upload_reminder_log')
                    .get(pk=event_detail_id)
                )
                reminder_log = getattr(locked, 'material_upload_reminder_log', None)
                if (
                    not reminder_log
                    or reminder_log.status != MaterialUploadReminderLog.Status.SENT
                    or reminder_log.follow_up_sent_at
                    or has_published_material(locked)
                ):
                    skipped += 1
                    continue
                recipient = get_material_reminder_recipient(locked)
                if not recipient or not recipient.email:
                    skipped += 1
                    continue
                if send_slide_reminder_email(locked):
                    reminder_log.follow_up_sent_at = timezone.now()
                    reminder_log.save(update_fields=['follow_up_sent_at'])
                    sent += 1
                    processed_ids.append(locked.pk)
                else:
                    failed += 1
        except Exception:
            logger.exception("資料公開リマインド処理エラー: EventDetail=%s", event_detail_id)
            failed += 1

    return SlideReminderResult(
        dry_run=False,
        candidates=len(candidate_ids),
        sent=sent,
        skipped=skipped,
        failed=failed,
        event_detail_ids=processed_ids,
    )
