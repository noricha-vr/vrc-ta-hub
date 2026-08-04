"""発表者アカウント紐づけ用の署名トークンを扱う。"""

from datetime import timedelta

from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

from event.models import EventDetail


SPEAKER_INVITE_MAX_AGE = timedelta(days=30)
SPEAKER_INVITE_SALT = "event.speaker-invite"
PAYLOAD_SEPARATOR = "|"


class SpeakerInviteTokenError(ValueError):
    """招待トークンを検証できない場合の基底例外。"""


class SpeakerInviteTokenExpired(SpeakerInviteTokenError):
    """招待トークンの有効期限が切れていることを示す。"""


class SpeakerInviteTokenInvalid(SpeakerInviteTokenError):
    """招待トークンが改ざんまたは破損していることを示す。"""


class SpeakerInviteTokenStale(SpeakerInviteTokenError):
    """招待発行後に対象の発表が更新されたことを示す。"""


def create_invite_token(event_detail: EventDetail) -> str:
    """保存済みの発表に対する30日間有効な署名トークンを作成する。

    Args:
        event_detail: 招待対象の発表。

    Returns:
        URL fragmentに埋め込める署名済みトークン。

    Raises:
        ValueError: 発表が未保存、または更新日時を持たない場合。
    """
    if event_detail.pk is None or event_detail.updated_at is None:
        raise ValueError("保存済みの発表のみ招待できます。")

    payload = PAYLOAD_SEPARATOR.join(
        (str(event_detail.pk), event_detail.updated_at.isoformat())
    )
    return TimestampSigner(salt=SPEAKER_INVITE_SALT).sign(payload)


def verify_invite_token(
    token: str,
    *,
    event_detail: EventDetail | None = None,
) -> EventDetail:
    """署名・期限・発表の更新世代を検証して対象を返す。

    Args:
        token: 招待URLから受け取った署名済みトークン。
        event_detail: ロック済みの対象を再検証する場合に指定する発表。

    Returns:
        検証済みの ``EventDetail``。

    Raises:
        SpeakerInviteTokenExpired: トークンの有効期限が切れている場合。
        SpeakerInviteTokenInvalid: トークンが改ざん、破損、または対象不明の場合。
        SpeakerInviteTokenStale: 発行後に対象の発表が更新された場合。
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

    if target.updated_at.isoformat() != generation:
        raise SpeakerInviteTokenStale
    return target


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
