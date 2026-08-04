"""ログイン後の既定リダイレクト先を決定する。"""

from django.conf import settings
from django.shortcuts import resolve_url
from django.urls import reverse


def get_default_login_redirect_url(user) -> str:
    """集会所属状況に応じたログイン後の既定リダイレクト先を返す。"""
    if user.community_memberships.exists():
        return resolve_url(settings.LOGIN_REDIRECT_URL)
    return reverse("event:my_presentations")
