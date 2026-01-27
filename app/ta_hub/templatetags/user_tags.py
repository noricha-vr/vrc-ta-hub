import logging

from django import template
from django.contrib.auth.models import AnonymousUser

from community.models import Community

register = template.Library()
logger = logging.getLogger(__name__)

# 集会名の表示最大文字数
COMMUNITY_NAME_MAX_LENGTH = 8


@register.simple_tag(takes_context=True)
def get_user_community_name(context):
    """ログインユーザーが所属する集会名を取得する。

    CommunityMemberを優先して確認し、なければ後方互換のcustom_user関連も確認する。

    Returns:
        集会名（8文字で切り捨て）。所属がない場合は空文字。
    """
    user = context['user']
    if not isinstance(user, AnonymousUser):
        # CommunityMemberを優先して確認
        membership = user.community_memberships.select_related('community').first()
        if membership:
            return membership.community.name[:COMMUNITY_NAME_MAX_LENGTH]
        # 後方互換: custom_userとして関連付けられた集会
        community = Community.objects.filter(custom_user_id=user.id).first()
        if community:
            return community.name[:COMMUNITY_NAME_MAX_LENGTH]
    return ''
