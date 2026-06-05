"""発表資料アップロード依頼メールの対象抽出と送信を扱う。"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q
from django.db import connections
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from event.models import EventDetail, MaterialUploadReminderLog
from website.constants import OPENROUTER_BASE_URL, build_openrouter_extra_headers, build_site_url

logger = logging.getLogger(__name__)

MATERIAL_UPLOAD_HISTORY_URL = "https://vrc-ta-hub.com/event/detail/history/"
DEFAULT_MATERIAL_REMINDER_MODEL = "google/gemini-2.5-flash-lite-preview-06-17"
MATERIAL_REMINDER_SYSTEM_PROMPT = """
あなたは発表後の資料アップロード依頼メールを送ってよいか判定する運営補助です。
発表申請時の備考を読み、資料・スライド・YouTube・動画・録画・アーカイブなどを
公開しない意向がある場合は shouldSend=false にしてください。

判断基準:
- 「資料公開なし」「スライド非公開」「YouTube公開なし」「動画公開なし」「アーカイブ不可」「録画NG」
  「配信しないでください」などは shouldSend=false。
- 「Youtube｜動画 公開なし。」のような短文、記号区切り、表記揺れも公開しない意向として扱う。
- 「資料は後日公開予定」は shouldSend=true。
- 「動画は公開なしだがスライドは共有可」は、メール目的が資料アップロード依頼のため shouldSend=true。
- 判断不能または曖昧で誤送信リスクがある場合は shouldSend=false。

必ず次のJSONだけを返してください:
{"shouldSend": true, "confidence": "high", "reason": "理由", "matchedIntent": "none"}
""".strip()


class MaterialReminderDecision(BaseModel):
    """LLM の送信可否判定を表す。"""

    should_send: bool = Field(alias="shouldSend")
    confidence: Literal["high", "medium", "low"]
    reason: str
    matched_intent: str = Field(alias="matchedIntent")


class MaterialReminderDecisionService(Protocol):
    """備考テキストから資料依頼メールの送信可否を判定する。"""

    def decide(self, note_text: str) -> MaterialReminderDecision:
        """Return whether to send a material upload reminder."""


@dataclass(frozen=True)
class ReminderResult:
    """1件の資料依頼処理結果。"""

    event_detail_id: int
    email: str
    action: str
    reason: str
    confidence: str = ""
    matched_intent: str = ""


class OpenRouterMaterialReminderDecisionService:
    """OpenRouter の OpenAI 互換 API で備考欄の公開可否意向を判定する。"""

    def __init__(self, *, model_name: str | None = None):
        self.model_name = model_name or getattr(settings, "GEMINI_MODEL", DEFAULT_MATERIAL_REMINDER_MODEL)

    def decide(self, note_text: str) -> MaterialReminderDecision:
        """Return whether to send a material upload reminder.

        Raises:
            ValueError: APIキー未設定、LLM応答不正、またはAPI呼び出し失敗の場合。
        """
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required for material reminder decision")

        model_name = self.model_name.split(":", 1)[0]
        if not any(connection.in_atomic_block for connection in connections.all()):
            connections.close_all()

        try:
            client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
            response = client.chat.completions.create(
                extra_headers=build_openrouter_extra_headers(),
                model=model_name,
                messages=[
                    {"role": "system", "content": MATERIAL_REMINDER_SYSTEM_PROMPT},
                    {"role": "user", "content": f"備考欄:\n{_sanitize_note_for_prompt(note_text)}"},
                ],
                temperature=0.1,
                max_tokens=400,
            )
        except Exception as exc:
            raise ValueError("material reminder LLM request failed") from exc

        text = response.choices[0].message.content or ""
        return parse_material_reminder_decision(text)


def parse_material_reminder_decision(text: str) -> MaterialReminderDecision:
    """Parse a material reminder decision from an LLM JSON response."""
    json_start = text.find("{")
    json_end = text.rfind("}") + 1
    if json_start == -1 or json_end <= json_start:
        raise ValueError("LLM decision JSON was not found")

    try:
        payload = json.loads(text[json_start:json_end])
        return MaterialReminderDecision.parse_obj(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError("LLM decision JSON is invalid") from exc


def iter_material_upload_reminder_targets(target_date: date):
    """Return approved presentation details that need a material upload reminder."""
    return (
        EventDetail.objects.select_related("event", "event__community", "applicant")
        .filter(
            detail_type="LT",
            status="approved",
            event__date=target_date,
            material_upload_reminder_log__isnull=True,
        )
        .filter(Q(slide_url="") | Q(slide_url__isnull=True))
        .filter(Q(slide_file="") | Q(slide_file__isnull=True))
        .order_by("event__start_time", "start_time", "id")
    )


def send_material_upload_reminders(
    *,
    target_date: date,
    dry_run: bool = False,
    decision_service: MaterialReminderDecisionService | None = None,
) -> list[ReminderResult]:
    """Send or preview material upload reminders for presentations on a date."""
    decision_service = decision_service or get_material_reminder_decision_service()
    results = []
    for event_detail in iter_material_upload_reminder_targets(target_date):
        results.append(
            process_material_upload_reminder_target(
                event_detail,
                dry_run=dry_run,
                decision_service=decision_service,
            )
        )
    return results


def get_material_reminder_decision_service() -> MaterialReminderDecisionService:
    """Build the configured material reminder decision service."""
    configured_service = getattr(settings, "MATERIAL_REMINDER_DECISION_SERVICE", None)
    if configured_service is not None:
        return configured_service
    return OpenRouterMaterialReminderDecisionService()


def process_material_upload_reminder_target(
    event_detail: EventDetail,
    *,
    dry_run: bool,
    decision_service: MaterialReminderDecisionService,
) -> ReminderResult:
    """Process one material upload reminder target."""
    applicant = get_material_reminder_recipient(event_detail)
    if not applicant or not applicant.email:
        return ReminderResult(event_detail.pk, "", "skipped_no_email", "申請者またはメールアドレスがありません")

    note_text = get_material_reminder_note_text(event_detail)
    try:
        decision = decision_service.decide(note_text)
    except Exception as exc:
        logger.exception(
            "発表資料アップロード依頼: LLM判定に失敗しました。EventDetail=%s",
            event_detail.pk,
        )
        return ReminderResult(event_detail.pk, applicant.email, "llm_error", str(exc))

    if not decision.should_send:
        if not dry_run:
            MaterialUploadReminderLog.objects.create(
                event_detail=event_detail,
                status=MaterialUploadReminderLog.Status.SKIPPED_BY_NOTE,
                reason=decision.reason,
                confidence=decision.confidence,
                matched_intent=decision.matched_intent,
            )
        return ReminderResult(
            event_detail.pk,
            applicant.email,
            "skipped_by_note",
            decision.reason,
            decision.confidence,
            decision.matched_intent,
        )

    if dry_run:
        return ReminderResult(
            event_detail.pk,
            applicant.email,
            "would_send",
            decision.reason,
            decision.confidence,
            decision.matched_intent,
        )

    try:
        _send_material_upload_reminder_email(event_detail)
    except Exception as exc:
        logger.exception(
            "発表資料アップロード依頼: メール送信に失敗しました。EventDetail=%s",
            event_detail.pk,
        )
        return ReminderResult(event_detail.pk, applicant.email, "mail_error", str(exc))

    MaterialUploadReminderLog.objects.create(
        event_detail=event_detail,
        status=MaterialUploadReminderLog.Status.SENT,
        reason=decision.reason,
        confidence=decision.confidence,
        matched_intent=decision.matched_intent,
        sent_at=timezone.now(),
    )
    return ReminderResult(
        event_detail.pk,
        applicant.email,
        "sent",
        decision.reason,
        decision.confidence,
        decision.matched_intent,
    )


def get_material_reminder_note_text(event_detail: EventDetail) -> str:
    """Return the applicant note used for the material reminder LLM decision."""
    presentation = _get_vket_presentation(event_detail)
    vket_note = presentation.participation.organizer_note if presentation else ""
    if vket_note:
        return vket_note
    return event_detail.additional_info or ""


def get_material_reminder_recipient(event_detail: EventDetail):
    """Return the user who should receive the material upload reminder."""
    if event_detail.applicant:
        return event_detail.applicant

    presentation = _get_vket_presentation(event_detail)
    if not presentation:
        return None
    return presentation.participation.applied_by


def _get_vket_presentation(event_detail: EventDetail):
    try:
        from vket.models import VketPresentation
    except ImportError:
        return None

    return (
        VketPresentation.objects.select_related("participation")
        .filter(published_event_detail=event_detail)
        .first()
    )


def _send_material_upload_reminder_email(event_detail: EventDetail) -> None:
    applicant = get_material_reminder_recipient(event_detail)
    if not applicant:
        raise ValueError(f"material upload reminder recipient was not found: event_detail={event_detail.pk}")

    upload_url = build_site_url(reverse("account:lt_application_edit", kwargs={"pk": event_detail.pk}))
    context = {
        "display_name": applicant.display_label,
        "event_detail": event_detail,
        "event": event_detail.event,
        "community": event_detail.event.community,
        "upload_url": upload_url,
        "history_url": MATERIAL_UPLOAD_HISTORY_URL,
    }
    html_message = render_to_string("event/email/material_upload_reminder.html", context)
    sent = send_mail(
        subject="発表資料アップロードのお願い",
        message=render_material_upload_reminder_text(context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[applicant.email],
        html_message=html_message,
    )
    if not sent:
        raise ValueError(f"material upload reminder mail was not sent: event_detail={event_detail.pk}")


def render_material_upload_reminder_text(context: dict[str, object]) -> str:
    """Render the plain-text material upload reminder body."""
    return (
        f"{context['display_name']} さん\n\n"
        "昨日は発表にご登壇いただき、ありがとうございました。\n"
        "おかげさまで、参加者にとって学びの多い時間になりました。\n\n"
        "当日の発表資料を共有できる場合は、資料のアップロードをお願いいたします。\n\n"
        "アップロード先：\n"
        f"{context['upload_url']}\n\n"
        "アップロードされた資料は、イベントページ上で参加者や後から見返したい方に向けて共有されます。\n"
        "実際の掲載イメージは、以下の過去イベントページをご確認ください。\n\n"
        "参考ページ：\n"
        f"{context['history_url']}\n\n"
        "お忙しいところ恐れ入りますが、どうぞよろしくお願いいたします。\n"
    )


def _sanitize_note_for_prompt(text: str, max_length: int = 2000) -> str:
    return " ".join((text or "").split())[:max_length]
