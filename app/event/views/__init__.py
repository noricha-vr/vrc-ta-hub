import logging

from event.libs import generate_blog

# event.views 直下の alias は既存 import と patch("event.views.*") の互換維持に使う。
logger = logging.getLogger(__name__)
_bigquery_client = None
_bigquery_project = None

from .blog import GenerateBlogView
from .crud import (
    EventDeleteView,
    EventDetailCreateView,
    EventDetailDeleteView,
    EventDetailUpdateView,
    GoogleCalendarEventCreateView,
)
from .detail import EventDetailView
from .helpers import (
    _can_applicant_edit_approved_lt,
    _get_bigquery_client,
    _parse_youtube_time,
    can_manage_event_detail,
    extract_video_id,
    extract_video_info,
)
from .list import EventDetailPastList, EventListView, EventLogListView, EventMyList
from .lt import (
    LTApplicationApproveView,
    LTApplicationCreateView,
    LTApplicationRejectView,
    LTApplicationReviewView,
)
from .sync import delete_outdated_events, register_calendar_events, sync_calendar_events

__all__ = [
    "EventDeleteView",
    "EventDetailCreateView",
    "EventDetailDeleteView",
    "EventDetailPastList",
    "EventDetailUpdateView",
    "EventDetailView",
    "EventListView",
    "EventLogListView",
    "EventMyList",
    "GenerateBlogView",
    "GoogleCalendarEventCreateView",
    "LTApplicationApproveView",
    "LTApplicationCreateView",
    "LTApplicationRejectView",
    "LTApplicationReviewView",
    "_bigquery_client",
    "_bigquery_project",
    "_can_applicant_edit_approved_lt",
    "_get_bigquery_client",
    "_parse_youtube_time",
    "can_manage_event_detail",
    "delete_outdated_events",
    "extract_video_id",
    "extract_video_info",
    "generate_blog",
    "logger",
    "register_calendar_events",
    "sync_calendar_events",
]
