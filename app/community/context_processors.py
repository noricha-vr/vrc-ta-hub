"""集会関連のコンテキストプロセッサ"""

import logging

from django.db import DatabaseError

logger = logging.getLogger(__name__)

_EMPTY_ACTIVE_COMMUNITY_CONTEXT = {
    'user_communities': [],
    'active_community': None,
    'active_membership': None,
}


def active_community(request):
    """アクティブな集会をテンプレートコンテキストに追加

    ログインユーザーが管理する集会の一覧と、現在アクティブな集会を
    テンプレートで利用可能にする。

    Returns:
        dict: 以下のキーを含む辞書
            - user_communities: ユーザーのCommunityMembership一覧
            - active_community: 現在アクティブなCommunityオブジェクト
            - active_membership: 現在アクティブなCommunityMemberオブジェクト
    """
    if not request.user.is_authenticated:
        return {'active_membership': None}

    try:
        memberships = list(request.user.community_memberships.select_related('community'))
    except DatabaseError:
        logger.exception(
            'Failed to load active community context for user_id=%s',
            getattr(request.user, 'id', None),
        )
        return _EMPTY_ACTIVE_COMMUNITY_CONTEXT.copy()

    if not memberships:
        return _EMPTY_ACTIVE_COMMUNITY_CONTEXT.copy()

    # セッションからactive_community_idを取得
    community_id = request.session.get('active_community_id')
    if community_id:
        active = next(
            (membership for membership in memberships if membership.community_id == community_id),
            None,
        )
        if active:
            return {
                'user_communities': memberships,
                'active_community': active.community,
                'active_membership': active
            }

    # デフォルト: 最初の集会
    first = memberships[0]
    if first:
        request.session['active_community_id'] = first.community_id
        return {
            'user_communities': memberships,
            'active_community': first.community,
            'active_membership': first
        }

    return _EMPTY_ACTIVE_COMMUNITY_CONTEXT.copy()
