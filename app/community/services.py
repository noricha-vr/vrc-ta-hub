"""アクティブ集会に関するビジネスロジック。

「アクティブ集会として受理してよいか」の判定はここ 1 箇所が正本。
GET 経路（event.views.my_list.EventMyList）と POST 経路
（community.views.member.SwitchCommunityView）の双方がこれを呼ぶ。
失敗理由は ActivationError で返し、黙って無視するか messages を出すかは
呼び出し側のポリシーに委ねる。
"""

from dataclasses import dataclass
from enum import Enum

from .models import Community


class ActivationError(Enum):
    """アクティブ集会として受理できなかった理由。"""

    NOT_SPECIFIED = 'not_specified'
    INVALID_ID = 'invalid_id'
    NOT_A_MEMBER = 'not_a_member'
    ENDED = 'ended'


@dataclass(frozen=True)
class ActivationResult:
    """activate_community の結果。

    Attributes:
        error: 失敗理由。成功時は None
        community: 受理した集会。失敗時は None
        session_updated: session を実際に書き換えたか（同値再代入時は False）
    """

    error: ActivationError | None = None
    community: Community | None = None
    session_updated: bool = False

    @property
    def is_accepted(self) -> bool:
        """受理できたかどうか。"""
        return self.error is None


SESSION_KEY = 'active_community_id'


def activate_community(session, user, raw_community_id) -> ActivationResult:
    """集会 ID を検証し、受理できればセッションのアクティブ集会を更新する。

    受理条件は「int に変換でき、user がメンバーで、終了済みでないこと」。

    Args:
        session: 更新対象のセッション（request.session）
        user: 操作中のユーザー
        raw_community_id: 未検証の集会 ID（クエリパラメータ / POST パラメータの生値）

    Returns:
        ActivationResult: 受理可否と失敗理由
    """
    if not raw_community_id:
        return ActivationResult(error=ActivationError.NOT_SPECIFIED)

    try:
        community_id = int(raw_community_id)
    except (TypeError, ValueError):
        return ActivationResult(error=ActivationError.INVALID_ID)

    membership = user.community_memberships.select_related('community').filter(
        community_id=community_id
    ).first()
    if membership is None:
        return ActivationResult(error=ActivationError.NOT_A_MEMBER)

    community = membership.community
    if community.is_ended:
        return ActivationResult(error=ActivationError.ENDED)

    # 同値の再代入でも session.modified が立ち毎回 DB 書き込みになるため差分がある時だけ更新
    # （my_list のページネーションリンクが community= を引き継ぐので全ページビューに乗る）
    session_updated = session.get(SESSION_KEY) != community_id
    if session_updated:
        session[SESSION_KEY] = community_id

    return ActivationResult(community=community, session_updated=session_updated)
