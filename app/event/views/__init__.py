"""event.views パッケージ

元の event/views.py を機能別モジュールに分割。
後方互換のため、全ビューをここから re-export する。
"""

from event.views.helpers import (  # noqa: F401
    _parse_youtube_time,
    can_manage_event_detail,
    extract_video_id,
    extract_video_info,
)
from event.views.list import (  # noqa: F401
    EventDetailPastList,
    EventListView,
    EventLogListView,
)
from event.views.my_list import EventMyList  # noqa: F401
from event.views.detail import EventDetailView  # noqa: F401
from event.views.crud import (  # noqa: F401
    EventDeleteView,
    EventDetailCreateView,
    EventDetailDeleteView,
    EventDetailUpdateView,
)
from event.views.calendar_create import GoogleCalendarEventCreateView  # noqa: F401
from event.views.blog import GenerateBlogView  # noqa: F401
from event.views.sync import (  # noqa: F401
    delete_outdated_events,
    register_calendar_events,
    sync_calendar_events,
)
from event.views.lt_application import (  # noqa: F401
    LTApplicationApproveView,
    LTApplicationCreateView,
    LTApplicationRejectView,
    LTApplicationReviewView,
)
