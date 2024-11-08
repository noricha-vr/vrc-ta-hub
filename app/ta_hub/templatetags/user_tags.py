import logging

from django import template
from django.contrib.auth.models import AnonymousUser

from community.models import Community

register = template.Library()
logger = logging.getLogger(__name__)


@register.simple_tag(takes_context=True)
def get_user_community_name(context):
    user = context['user']
    if not isinstance(user, AnonymousUser):
        logger.info(f'user: {user}')
        community = Community.objects.filter(custom_user_id=user.id).first()
        if not community:
            return ''
        return community.name[:8]  # 8文字で切り捨て
    return ''
