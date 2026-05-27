import re

from django.core.exceptions import ValidationError


VRCHAT_USER_ID_RE = re.compile(
    r'\Ausr_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z',
    re.IGNORECASE,
)
VRCHAT_USER_URL_RE = re.compile(
    r'\Ahttps?://(?:www\.)?vrchat\.com/home/user/'
    r'(?P<user_id>usr_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
    r'(?:[/?#].*)?\Z',
    re.IGNORECASE,
)


def normalize_vrchat_user_id(value: str) -> str:
    """VRChatユーザーURLまたはIDを保存用のユーザーIDに正規化する。"""
    if not value:
        return ''

    text = value.strip()
    url_match = VRCHAT_USER_URL_RE.match(text)
    if url_match:
        return url_match.group('user_id').lower()

    if VRCHAT_USER_ID_RE.match(text):
        return text.lower()

    raise ValidationError(
        'VRChatユーザーIDまたはプロフィールURLを入力してください。'
        '例: usr_01b02b0e-58b5-4558-a6ca-56dd32dafdad'
    )
