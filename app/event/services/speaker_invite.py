"""発表者アカウント紐づけ用の署名トークンを扱う。"""

from datetime import timedelta

from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

from event.models import EventDetail


SPEAKER_INVITE_MAX_AGE = timedelta(days=7)
SPEAKER_INVITE_SALT = "event.speaker-invite"
PAYLOAD_SEPARATOR = "|"


class SpeakerInviteTokenError(ValueError):
    """招待トークンを検証できない場合の基底例外。"""


class SpeakerInviteTokenExpired(SpeakerInviteTokenError):
    """招待トークンの有効期限が切れていることを示す。"""


class SpeakerInviteTokenInvalid(SpeakerInviteTokenError):
    """招待トークンが改ざんまたは破損していることを示す。"""


class SpeakerInviteTokenStale(SpeakerInviteTokenError):
    """招待発行後に対象の紐づけ状態が変わったことを示す。"""


def create_invite_token(event_detail: EventDetail) -> str:
    """保存済みの発表に対する7日間有効な署名トークンを作成する。

    Args:
        event_detail: 招待対象の発表。

    Returns:
        URL fragmentに埋め込める署名済みトークン。

    Raises:
        ValueError: 発表が未保存の場合。
    """
    if event_detail.pk is None:
        raise ValueError("保存済みの発表のみ招待できます。")

    payload = PAYLOAD_SEPARATOR.join(
        (str(event_detail.pk), _applicant_generation(event_detail.applicant_id))
    )
    return TimestampSigner(salt=SPEAKER_INVITE_SALT).sign(payload)


def verify_invite_token(
    token: str,
    *,
    event_detail: EventDetail | None = None,
) -> EventDetail:
    """署名・期限・発表の紐づけ世代を検証して対象を返す。

    Args:
        token: 招待URLから受け取った署名済みトークン。
        event_detail: ロック済みの対象を再検証する場合に指定する発表。

    Returns:
        検証済みの ``EventDetail``。

    Raises:
        SpeakerInviteTokenExpired: トークンの有効期限が切れている場合。
        SpeakerInviteTokenInvalid: トークンが改ざん、破損、または対象不明の場合。
        SpeakerInviteTokenStale: 発行後に対象の紐づけ状態が変わった場合。
    """
    event_detail_id, generation = _unsign_payload(token)
    target = event_detail
    if target is None:
        try:
            target = EventDetail.objects.select_related(
                "event__community", "applicant"
            ).get(pk=event_detail_id)
        except EventDetail.DoesNotExist as exc:
            raise SpeakerInviteTokenInvalid from exc
    elif target.pk != event_detail_id:
        raise SpeakerInviteTokenInvalid

    if generation != _applicant_generation(target.applicant_id):
        raise SpeakerInviteTokenStale
    if target.applicant_id is not None:
        raise SpeakerInviteTokenStale
    return target


def _applicant_generation(applicant_id: int | None) -> str:
    """紐づけ状態をトークン世代へ変換する。"""
    # 失効はクレームと期限のみ。解除後も旧リンクを無効化する必要が生じたら、
    # nonce フィールドを追加する migration で対応する。
    return f"applicant:{applicant_id or ''}"


def _unsign_payload(token: str) -> tuple[int, str]:
    signer = TimestampSigner(salt=SPEAKER_INVITE_SALT)
    try:
        value = signer.unsign(token, max_age=SPEAKER_INVITE_MAX_AGE)
    except SignatureExpired as exc:
        raise SpeakerInviteTokenExpired from exc
    except BadSignature as exc:
        raise SpeakerInviteTokenInvalid from exc

    try:
        event_detail_id, generation = value.split(PAYLOAD_SEPARATOR, maxsplit=1)
        return int(event_detail_id), generation
    except (AttributeError, TypeError, ValueError) as exc:
        raise SpeakerInviteTokenInvalid from exc
