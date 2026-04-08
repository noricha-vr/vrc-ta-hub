from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.sites.shortcuts import get_current_site


def is_discord_oauth_available(request=None) -> bool:
    """Discord OAuth を安全に呼べる状態かを返す."""
    provider_settings = getattr(settings, 'SOCIALACCOUNT_PROVIDERS', {}).get('discord', {})
    if provider_settings.get('APPS'):
        return True

    site = get_current_site(request) if request is not None else get_current_site(None)
    return SocialApp.objects.filter(provider='discord', sites=site).exists()
