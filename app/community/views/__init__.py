"""community ビューパッケージ.

全ビューを re-export して後方互換を維持する。
urls.py や他モジュールからの `from community.views import XxxView` をそのまま使える。
"""
from .public import (  # noqa: F401
    CommunityListView,
    CommunityDetailView,
    ArchivedCommunityListView,
)
from .manage import (  # noqa: F401
    CommunityUpdateView,
    CommunityCreateView,
    WaitingCommunityListView,
    AcceptView,
    RejectView,
    CloseCommunityView,
    AdminCommunityCleanupView,
    ReopenCommunityView,
)
from .member import (  # noqa: F401
    SwitchCommunityView,
    CommunityMemberManageView,
    RemoveStaffView,
    CreateInvitationView,
    RevokeInvitationView,
    AcceptInvitationView,
)
from .settings import (  # noqa: F401
    CommunitySettingsView,
    CreateOwnershipTransferView,
    AcceptOwnershipTransferView,
    RevokeOwnershipTransferView,
    UpdateWebhookView,
    TestWebhookView,
    LTApplicationListView,
    UpdateLTSettingsView,
)
from .report import CommunityReportView  # noqa: F401
