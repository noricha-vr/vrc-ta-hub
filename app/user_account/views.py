"""Account view public imports.

既存の `from user_account.views import XxxView` を維持しながら、
実装は責務別の view_modules に分割する。
"""

from .view_modules.api_keys import APIKeyCreateView, APIKeyDeleteView, APIKeyListView
from .view_modules.lt_applications import LTApplicationEditView, LTApplicationListView
from .view_modules.profile import SettingsView, UserNameChangeView, UserUpdateView
from .view_modules.session import (
    CustomLoginView,
    CustomLogoutView,
    CustomPasswordChangeView,
    DiscordRequiredView,
    RegisterView,
)

__all__ = [
    'APIKeyCreateView',
    'APIKeyDeleteView',
    'APIKeyListView',
    'CustomLoginView',
    'CustomLogoutView',
    'CustomPasswordChangeView',
    'DiscordRequiredView',
    'LTApplicationEditView',
    'LTApplicationListView',
    'RegisterView',
    'SettingsView',
    'UserNameChangeView',
    'UserUpdateView',
]
