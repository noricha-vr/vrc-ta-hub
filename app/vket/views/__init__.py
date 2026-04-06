"""vket.views パッケージ -- 後方互換のため全ビューを re-export する。"""

from .apply import ApplyView
from .helpers import (
    PHASE_SORT_ORDER,
    Slot,
    _apply_permissions_for_user,
    _build_schedule_context,
    _get_active_membership,
    _get_visible_collaborations,
    _is_vket_admin,
    _shift_time,
    _time_ranges_overlap,
)
from .manage import (
    ManageParticipationUpdateView,
    ManageScheduleView,
    ManageView,
)
from .notice import (
    AckNoticeView,
    ManageNoticeCreateView,
    ManageNoticeListView,
    ManageNoticeUpdateView,
    NoticeListView,
)
from .presentation import (
    ManagePresentationDeleteView,
    PresentationDeleteView,
    _delete_presentation,
)
from .public import (
    CollaborationDetailView,
    CollaborationListView,
)
from .publish import ManagePublishView
from .status import (
    ParticipationStatusView,
    StageRegisterView,
    VketStatusRedirectView,
)

__all__ = [
    # helpers
    'PHASE_SORT_ORDER',
    'Slot',
    '_apply_permissions_for_user',
    '_build_schedule_context',
    '_get_active_membership',
    '_get_visible_collaborations',
    '_is_vket_admin',
    '_shift_time',
    '_time_ranges_overlap',
    # public
    'CollaborationDetailView',
    'CollaborationListView',
    # apply
    'ApplyView',
    # manage
    'ManageParticipationUpdateView',
    'ManageScheduleView',
    'ManageView',
    # notice
    'AckNoticeView',
    'ManageNoticeCreateView',
    'ManageNoticeListView',
    'ManageNoticeUpdateView',
    'NoticeListView',
    # presentation
    'ManagePresentationDeleteView',
    'PresentationDeleteView',
    '_delete_presentation',
    # publish
    'ManagePublishView',
    # status
    'ParticipationStatusView',
    'StageRegisterView',
    'VketStatusRedirectView',
]
