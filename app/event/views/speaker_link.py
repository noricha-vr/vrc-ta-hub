"""発表者本人によるアカウント紐づけを提供するビュー。"""

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from event.models import EventDetail
from event.notifications import notify_speaker_account_linked
from event.services.speaker_invite import (
    SpeakerInviteTokenExpired,
    SpeakerInviteTokenInvalid,
    SpeakerInviteTokenStale,
    create_invite_token,
    verify_invite_token,
)


SPEAKER_INVITE_SESSION_KEY = "speaker_invite_token"
EXPIRED_INVITE_MESSAGE = (
    "この招待リンクの有効期限（7日）が切れています。"
    "主催者に新しいリンクを依頼してください。"
)
INVALID_INVITE_MESSAGE = (
    "招待リンクが正しくありません。URLが途中で切れていないか確認してください。"
)
STALE_INVITE_MESSAGE = (
    "この招待リンクは現在のアカウント紐づけ状態では利用できません。"
    "主催者に新しいリンクを依頼してください。"
)
ALREADY_LINKED_MESSAGE = "この発表には既にアカウントが紐づいています。"
NOT_INVITABLE_MESSAGE = "承認済みの発表のみアカウントを紐づけられます。"


def _event_detail_queryset() -> QuerySet[EventDetail]:
    return EventDetail.objects.select_related("event__community", "applicant")


def _is_invitable(event_detail: EventDetail) -> bool:
    return event_detail.detail_type == "LT" and event_detail.status == "approved"


def _protect_sensitive_response(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


def _is_ajax(request: HttpRequest) -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _invite_issue_response(
    request: HttpRequest,
    event_detail: EventDetail,
    payload: dict[str, str],
    *,
    status: int = 200,
) -> HttpResponse:
    """招待発行結果をリクエスト形式に応じて返す。"""
    if _is_ajax(request):
        return JsonResponse(payload, status=status)

    if status >= 400:
        messages.error(request, payload["error"])
    else:
        messages.warning(
            request,
            "招待リンクの発行にはJavaScriptが必要です。"
            "JavaScriptを有効にして再度発行してください。",
        )
    return redirect("event:detail", pk=event_detail.pk)


class SensitiveResponseMixin:
    """トークン交換に関係するレスポンスの保存と参照元送信を防ぐ。"""

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        response = super().dispatch(request, *args, **kwargs)
        return _protect_sensitive_response(response)


class SpeakerInviteIssueView(SensitiveResponseMixin, View):
    """集会管理者に発表者向けの招待URLを発行する。"""

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        event_detail = get_object_or_404(_event_detail_queryset(), pk=pk)
        community = event_detail.event.community
        if not request.user.is_authenticated or not community.can_edit(request.user):
            if _is_ajax(request):
                return JsonResponse({"error": "権限がありません。"}, status=403)
            return HttpResponse("権限がありません。", status=403)
        if not _is_invitable(event_detail):
            return _invite_issue_response(
                request,
                event_detail,
                {"error": NOT_INVITABLE_MESSAGE},
                status=400,
            )
        if event_detail.applicant_id is not None:
            return _invite_issue_response(
                request,
                event_detail,
                {"error": ALREADY_LINKED_MESSAGE},
                status=409,
            )
        if not _is_ajax(request):
            return _invite_issue_response(request, event_detail, {})

        token = create_invite_token(event_detail)
        confirm_path = reverse("event:speaker_link_confirm")
        invite_url = f"{request.build_absolute_uri(confirm_path)}#{token}"
        return _invite_issue_response(
            request,
            event_detail,
            {"invite_url": invite_url},
        )


class SpeakerInviteTokenExchangeView(SensitiveResponseMixin, View):
    """URL fragmentからPOSTされたトークンをサーバーセッションへ移す。"""

    def post(self, request: HttpRequest) -> JsonResponse:
        token = request.POST.get("token", "")
        if not token:
            return JsonResponse({"error": INVALID_INVITE_MESSAGE}, status=400)
        try:
            event_detail = verify_invite_token(token)
        except SpeakerInviteTokenExpired:
            return JsonResponse({"error": EXPIRED_INVITE_MESSAGE}, status=400)
        except SpeakerInviteTokenStale:
            return JsonResponse({"error": STALE_INVITE_MESSAGE}, status=400)
        except SpeakerInviteTokenInvalid:
            return JsonResponse({"error": INVALID_INVITE_MESSAGE}, status=400)

        if not _is_invitable(event_detail):
            return JsonResponse({"error": NOT_INVITABLE_MESSAGE}, status=400)
        if event_detail.applicant_id is not None:
            return JsonResponse({"error": ALREADY_LINKED_MESSAGE}, status=409)

        request.session[SPEAKER_INVITE_SESSION_KEY] = token
        return JsonResponse({"confirm_url": reverse("event:speaker_link_confirm")})


class SpeakerLinkConfirmView(SensitiveResponseMixin, View):
    """セッション内の招待を確認し、ログイン中ユーザーを発表へ紐づける。"""

    template_name = "event/speaker_link_confirm.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        token = request.session.get(SPEAKER_INVITE_SESSION_KEY)
        if not token:
            return render(
                request,
                self.template_name,
                {"needs_token_exchange": True},
            )
        if not request.user.is_authenticated:
            return redirect_to_login(
                request.get_full_path(),
                login_url=reverse("account:login"),
            )
        try:
            event_detail = verify_invite_token(token)
        except SpeakerInviteTokenExpired:
            return self._clear_and_render_error(request, EXPIRED_INVITE_MESSAGE)
        except SpeakerInviteTokenStale:
            return self._clear_and_render_error(request, STALE_INVITE_MESSAGE)
        except SpeakerInviteTokenInvalid:
            return self._clear_and_render_error(request, INVALID_INVITE_MESSAGE)

        if not _is_invitable(event_detail):
            return self._clear_and_render_error(request, NOT_INVITABLE_MESSAGE)
        if event_detail.applicant_id is not None:
            return self._clear_and_render_error(
                request,
                ALREADY_LINKED_MESSAGE,
                event_detail=event_detail,
            )
        return render(
            request,
            self.template_name,
            {"event_detail": event_detail, "can_confirm": True},
        )

    def post(self, request: HttpRequest) -> HttpResponse:
        if not request.user.is_authenticated:
            return redirect_to_login(
                request.get_full_path(),
                login_url=reverse("account:login"),
            )
        token = request.session.get(SPEAKER_INVITE_SESSION_KEY)
        if not token:
            return self._render_error(request, INVALID_INVITE_MESSAGE)
        try:
            verified_detail = verify_invite_token(token)
            with transaction.atomic():
                event_detail = _event_detail_queryset().select_for_update().get(
                    pk=verified_detail.pk
                )
                verify_invite_token(token, event_detail=event_detail)
                if not _is_invitable(event_detail):
                    return self._clear_and_render_error(
                        request, NOT_INVITABLE_MESSAGE
                    )
                if event_detail.applicant_id is not None:
                    return self._clear_and_render_error(
                        request,
                        ALREADY_LINKED_MESSAGE,
                        event_detail=event_detail,
                    )
                event_detail.applicant = request.user
                event_detail.save(update_fields=["applicant", "updated_at"])
                linked_user = request.user
                transaction.on_commit(
                    lambda: notify_speaker_account_linked(event_detail, linked_user)
                )
        except SpeakerInviteTokenExpired:
            return self._clear_and_render_error(request, EXPIRED_INVITE_MESSAGE)
        except SpeakerInviteTokenStale:
            return self._clear_and_render_error(request, STALE_INVITE_MESSAGE)
        except (SpeakerInviteTokenInvalid, EventDetail.DoesNotExist):
            return self._clear_and_render_error(request, INVALID_INVITE_MESSAGE)

        request.session.pop(SPEAKER_INVITE_SESSION_KEY, None)
        messages.success(request, "発表とアカウントを紐づけました。")
        return redirect("event:detail", pk=event_detail.pk)

    def _clear_and_render_error(
        self,
        request: HttpRequest,
        message: str,
        *,
        event_detail: EventDetail | None = None,
    ) -> HttpResponse:
        request.session.pop(SPEAKER_INVITE_SESSION_KEY, None)
        return self._render_error(request, message, event_detail=event_detail)

    def _render_error(
        self,
        request: HttpRequest,
        message: str,
        *,
        event_detail: EventDetail | None = None,
    ) -> HttpResponse:
        return render(
            request,
            self.template_name,
            {
                "event_detail": event_detail,
                "error_message": message,
                "can_confirm": False,
            },
        )


class SpeakerLinkUnlinkView(View):
    """集会管理者が発表とアカウントの紐づけを解除する。"""

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        with transaction.atomic():
            event_detail = get_object_or_404(
                _event_detail_queryset().select_for_update(),
                pk=pk,
            )
            community = event_detail.event.community
            if not request.user.is_authenticated or not community.can_edit(request.user):
                return HttpResponse("権限がありません。", status=403)
            if not _is_invitable(event_detail):
                messages.error(request, NOT_INVITABLE_MESSAGE)
                return redirect("event:detail", pk=event_detail.pk)
            event_detail.applicant = None
            event_detail.save(update_fields=["applicant", "updated_at"])

        messages.success(request, "発表とアカウントの紐づけを解除しました。")
        return redirect("event:detail", pk=event_detail.pk)
