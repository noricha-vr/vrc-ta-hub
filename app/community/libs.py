import logging
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

VRC_GROUP_DOMAIN = "vrc.group"
VRCHAT_API_REDIRECT_URL = "https://api.vrchat.cloud/api/1/groups/redirect/{code}"
VRCHAT_BASE_URL = "https://vrchat.com"
USER_AGENT = "vrc-ta-hub/1.0 noricha-vr"
REQUEST_TIMEOUT_SECONDS = 5


def resolve_vrc_group_url(url: str) -> str:
    """vrc.group 短縮URLを vrchat.com 正規URLに変換する。

    短縮URLでない場合はそのまま返す。
    解決失敗時はそのまま返す（Fail Soft）。

    Args:
        url: VRChatグループURL（短縮URLまたは正規URL）

    Returns:
        正規URL、または解決できなかった場合は元のURL
    """
    if not url:
        return url

    parsed = urlparse(url)
    if parsed.hostname != VRC_GROUP_DOMAIN:
        return url

    # 短縮コードを抽出（パスから先頭の / を除去）
    code = parsed.path.lstrip("/")
    if not code:
        return url

    redirect_url = VRCHAT_API_REDIRECT_URL.format(code=code)
    try:
        response = requests.head(
            redirect_url,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        location = response.headers.get("Location", "")
        if location and "/home/group/" in location:
            return f"{VRCHAT_BASE_URL}{location}"
    except requests.RequestException as e:
        logger.warning("vrc.group URL解決に失敗: url=%s, error=%s", url, e)

    return url


def get_join_type(join_str: str) -> str:
    if join_str.find('group/') != -1:
        return 'group'
    elif join_str.find('/custom_user/') != -1:
        return 'user_page'
    elif join_str.find('vrch.at/') != -1:
        return 'world'
    else:
        return 'user_name'
