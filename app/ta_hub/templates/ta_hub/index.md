# VRC技術学術ハブ - {{ current_date|date:"Y年n月j日" }}
{% if database_degraded %}
> データベース障害のため一時的に一覧を表示できません。/api/v1/ を参照してください。
{% endif %}
## 今週の発表 ({{ markdown_event_details|length }}件)
{% for detail in markdown_event_details %}
- {{ detail.event.date|date:"n/j (D)" }} {{ detail.start_time|time:"H:i" }}〜: 「{{ detail.theme }}」 by {{ detail.speaker }} @ [{{ detail.event.community.name }}]({{ site_base }}community/{{ detail.event.community.pk }}/)
{% endfor %}

## 今週の集会 ({{ markdown_events|length }}件)
{% for event in markdown_events %}
- {{ event.date|date:"n/j (D)" }} {{ event.start_time|time:"H:i" }}〜{{ event.end_time|time:"H:i" }}: [{{ event.community.name }}]({{ site_base }}community/{{ event.community.pk }}/)
{% endfor %}

## 特別企画
{% for special in markdown_special_events %}
- [{{ special.h1|default:special.theme }}]({{ site_base }}event/detail/{{ special.pk }}/) — {{ special.event.date|date:"Y-m-d" }}
{% endfor %}

---

このページは LLM 向けに最適化された Markdown 版です。人向け HTML は [{{ site_base }}]({{ site_base }}) です。
