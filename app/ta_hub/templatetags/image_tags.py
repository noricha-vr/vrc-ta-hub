from django import template

from ta_hub.libs import cloudflare_image_url

register = template.Library()


@register.filter
def cf_resize(url, width):
    """Cloudflare Image Resizing で画像をリサイズする。

    Usage: {{ community.poster_image.url|cf_resize:"400" }}
    """
    try:
        width = int(width)
    except (ValueError, TypeError):
        return url
    return cloudflare_image_url(url, width)
