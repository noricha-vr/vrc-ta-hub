"""集会関連のコンテキストプロセッサ"""


def active_community(request):
    """アクティブな集会をテンプレートコンテキストに追加

    ログインユーザーが管理する集会の一覧と、現在アクティブな集会を
    テンプレートで利用可能にする。

    Returns:
        dict: 以下のキーを含む辞書
            - user_communities: ユーザーのCommunityMembershipのクエリセット
            - active_community: 現在アクティブなCommunityオブジェクト
            - active_membership: 現在アクティブなCommunityMemberオブジェクト
    """
    if not request.user.is_authenticated:
        return {'active_membership': None}

    memberships = request.user.community_memberships.select_related('community')
    if not memberships.exists():
        return {'user_communities': [], 'active_community': None, 'active_membership': None}

    # セッションからactive_community_idを取得
    community_id = request.session.get('active_community_id')
    if community_id:
        active = memberships.filter(community_id=community_id).first()
        if active:
            return {
                'user_communities': memberships,
                'active_community': active.community,
                'active_membership': active
            }

    # デフォルト: 最初の集会
    first = memberships.first()
    if first:
        request.session['active_community_id'] = first.community_id
        return {
            'user_communities': memberships,
            'active_community': first.community,
            'active_membership': first
        }

    return {'user_communities': [], 'active_community': None, 'active_membership': None}
