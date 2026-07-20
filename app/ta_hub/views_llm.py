import logging
import re

from django.db.utils import OperationalError
from django.utils import timezone
from django.views.generic import TemplateView

from ta_hub.index_cache import get_index_view_cache_key
from ta_hub.views import IndexView
from utils.vrchat_time import get_vrchat_today

logger = logging.getLogger(__name__)

_MARKDOWN_SPECIAL_CHARACTERS = re.compile(r"([\\\\`*_{}\[\]<>()#+\-.!|])")


def _escape_markdown_text(value):
    """Return user-provided text without Markdown structure."""
    normalized = str(value or "").replace("\r", " ").replace("\n", " ")
    return _MARKDOWN_SPECIAL_CHARACTERS.sub(lambda match: f"\\{match.group(1)}", normalized)


def _build_markdown_event(event):
    """Copy an event payload with display-safe community data."""
    return {
        **event,
        "community": {
            "pk": event["community"].pk,
            "name": _escape_markdown_text(event["community"].name),
        },
    }


def _build_markdown_context(database_context, today):
    """Limit cached records to this week and escape Markdown display fields."""
    week_end = today + timezone.timedelta(days=7)
    markdown_events = [
        _build_markdown_event(event)
        for event in database_context["upcoming_events"]
        if event["date"] <= week_end
    ]
    markdown_event_details = [
        {
            **detail,
            "event": _build_markdown_event(detail["event"]),
            "speaker": _escape_markdown_text(detail["speaker"]),
            "theme": _escape_markdown_text(detail["theme"]),
        }
        for detail in database_context["upcoming_event_details"]
        if detail["event"]["date"] <= week_end
    ]
    markdown_special_events = [
        {
            **special,
            "event": _build_markdown_event(special["event"]),
            "h1": _escape_markdown_text(special["h1"]),
            "theme": _escape_markdown_text(special["theme"]),
        }
        for special in database_context["special_events"]
        if special["event"]["date"] <= week_end
    ]
    return {
        "markdown_events": markdown_events,
        "markdown_event_details": markdown_event_details,
        "markdown_special_events": markdown_special_events,
    }


class LlmsTxtView(TemplateView):
    template_name = 'ta_hub/llms.txt'
    content_type = 'text/markdown; charset=utf-8'


class IndexMarkdownView(TemplateView):
    template_name = 'ta_hub/index.md'
    content_type = 'text/markdown; charset=utf-8'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = get_vrchat_today()
        context['current_date'] = timezone.localdate()
        context['database_degraded'] = False
        context['upcoming_events'] = []
        context['upcoming_event_details'] = []
        context['special_events'] = []

        try:
            database_context = IndexView._build_database_context(
                self,
                today,
                get_index_view_cache_key(today),
            )
            context.update(database_context)
            context.update(_build_markdown_context(database_context, today))
        except OperationalError as exc:
            logger.warning(
                "IndexMarkdownView degraded gracefully because the database was unavailable: %s",
                exc,
            )
            context['database_degraded'] = True

        return context
