from django import template

register = template.Library()

STATUS_BADGE_COLORS = {
    'generating': 'info',
    'generation_failed': 'warning',
    'ready': 'primary',
    'posted': 'success',
    'failed': 'danger',
}


@register.filter
def status_badge_color(status):
    """TweetQueue のステータスに応じた Bootstrap バッジカラーを返す。"""
    return STATUS_BADGE_COLORS.get(status, 'secondary')
