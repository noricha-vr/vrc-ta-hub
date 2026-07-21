import logging
import re

from django.db.utils import OperationalError
from django.utils import timezone
from django.views.generic import TemplateView

from ta_hub.index_cache import build_index_database_context, get_index_view_cache_key
from utils.vrchat_time import get_vrchat_today

logger = logging.getLogger(__name__)

# Markdown 構文に効く特殊文字。ユーザー入力から見出し・リンク・強調・打消し等を生成させないようエスケープする
_MARKDOWN_SPECIAL_CHARACTERS = re.compile(r"([\\`*_{}\[\]<>()#+\-.!|~])")


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
    """Escape Markdown display fields for cached records.

    week 絞り込みは以下の方針:
    - upcoming_event_details: DB クエリは event__date__lte を持たないため、ここで week_end を適用する
    - upcoming_events: DB クエリ側で date__lte=end_date 済みだが、キャッシュ寿命との兼ね合いで
      境界日をまたいだ古いエントリが残る可能性に備え、防御的に week_end を適用する
    - special_events: DB クエリ側は上限日を持たず「今日以降」のみ絞る。Markdown 面でも上限を設けず
      表示する（LLM が特別企画の存在を把握できるようにする方針。PR #515 のフォローアップ）
    """
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
    ]
    return {
        "markdown_events": markdown_events,
        "markdown_event_details": markdown_event_details,
        "markdown_special_events": markdown_special_events,
    }


class MarkdownTemplateView(TemplateView):
    """Provide an absolute site URL to Markdown templates."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['site_base'] = self.request.build_absolute_uri('/')
        return context


class LlmsTxtView(MarkdownTemplateView):
    template_name = 'ta_hub/llms.txt'
    content_type = 'text/markdown; charset=utf-8'


class IndexMarkdownView(MarkdownTemplateView):
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
        context['markdown_events'] = []
        context['markdown_event_details'] = []
        context['markdown_special_events'] = []

        try:
            database_context = build_index_database_context(
                self.request,
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
