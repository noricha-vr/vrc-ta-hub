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

    Returns:
        集会名（8文字で切り捨て）。所属がない場合は空文字。
    """
    user = context['user']
    if not isinstance(user, AnonymousUser):
        membership = user.community_memberships.select_related('community').first()
        if membership:
            return membership.community.name[:COMMUNITY_NAME_MAX_LENGTH]
    return ''
