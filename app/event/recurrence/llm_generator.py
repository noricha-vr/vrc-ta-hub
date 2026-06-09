"""LLM経由の日付生成ロジック

custom_rule が deterministic に解釈できない場合のフォールバックとして
EventDateLlmService を呼び出して開催日リストを推論させる。

公開関数:
    - get_recent_events_history(rule, base_date, community=None)
    - generate_dates_by_llm(rule, base_date, base_time, months, community, llm_service)
"""
import datetime
import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING, List, Optional

from django.db import models

from event.llm_service import EventDateLlmService
from event.models import Event, RecurrenceRule

from event.recurrence.calculator import get_japanese_weekday, get_week_of_month

if TYPE_CHECKING:
    from community.models import Community

# 既存テストが `event.recurrence_service` 名のロガーをアサートするため、
# 物理的にこのモジュールから出力するログも同じロガー名で出す。
logger = logging.getLogger("event.recurrence_service")


def get_recent_events_history(
    rule: RecurrenceRule,
    base_date: date,
    community: Optional["Community"] = None,
) -> str:
    """直近5回の開催履歴を取得してフォーマットする"""
    try:
        recent_events = []

        # このルールに関連するイベントを取得
        if rule.id:  # 既存のルールの場合
            # RecurrenceRuleに紐づくマスターイベントを探す
            master_events = Event.objects.filter(recurrence_rule=rule, is_recurring_master=True)

            if master_events.exists():
                # マスターイベントに紐づく過去のイベントを取得
                for master_event in master_events:
                    # マスターイベント自身と、そのインスタンスを取得
                    events = Event.objects.filter(
                        models.Q(id=master_event.id) | models.Q(recurring_master=master_event),
                        date__lt=base_date
                    ).order_by('-date')[:5]
                    recent_events.extend(events)

                # 全体でソートして最新5件を取得
                recent_events = sorted(recent_events, key=lambda e: e.date, reverse=True)[:5]

        # ルールに紐づくイベントがない場合、またはcommunityが指定されている場合
        if not recent_events and community:
            # コミュニティの過去のイベントから取得
            recent_events = Event.objects.filter(
                community=community,
                date__lt=base_date
            ).order_by('-date')[:5]

        if not recent_events:
            return "過去の開催履歴: なし"

        # 履歴をフォーマット
        history_lines = ["過去の開催履歴（直近5回）:"]
        for event in reversed(recent_events):  # 古い順に表示
            weekday = get_japanese_weekday(event.date.weekday())
            week = get_week_of_month(event.date)
            history_lines.append(
                f"- {event.date.strftime('%Y-%m-%d')} ({weekday}) 第{week}週"
            )

        # パターン分析を追加
        if len(recent_events) >= 2:
            history_lines.append("\n開催パターン分析:")

            # 曜日の傾向
            weekdays = [e.date.weekday() for e in recent_events]
            weekday_counts = {}
            for wd in weekdays:
                wd_name = get_japanese_weekday(wd)
                weekday_counts[wd_name] = weekday_counts.get(wd_name, 0) + 1

            if weekday_counts:
                most_common_weekday = max(weekday_counts, key=weekday_counts.get)
                history_lines.append(f"- 主な開催曜日: {most_common_weekday}")

            # 週の傾向（第何週か）
            weeks = [get_week_of_month(e.date) for e in recent_events]
            week_counts = {}
            for w in weeks:
                week_counts[w] = week_counts.get(w, 0) + 1

            if week_counts:
                most_common_week = max(week_counts, key=week_counts.get)
                history_lines.append(f"- 主な開催週: 第{most_common_week}週")

            # 間隔の分析
            if len(recent_events) >= 2:
                intervals = []
                for i in range(len(recent_events) - 1):
                    interval = (recent_events[i].date - recent_events[i + 1].date).days
                    intervals.append(interval)

                if intervals:
                    avg_interval = sum(intervals) / len(intervals)
                    history_lines.append(f"- 平均開催間隔: {avg_interval:.1f}日")

                    # 隔週パターンの検出
                    if 12 <= avg_interval <= 16:
                        history_lines.append("- パターン: 隔週開催の可能性が高い")
                    elif 27 <= avg_interval <= 32:
                        history_lines.append("- パターン: 月1回開催の可能性が高い")

        return "\n".join(history_lines)

    except Exception:
        logger.exception("Error getting recent events history")
        return "過去の開催履歴: 取得エラー"


def build_llm_prompt(
    rule: RecurrenceRule,
    base_date: date,
    base_time: datetime.time,
    end_date: date,
    recent_events_history: str,
) -> str:
    """LLM 用のプロンプトを構築する"""
    return f"""
以下の条件で定期イベントの日付リストを生成してください。

基準日: {base_date.strftime('%Y-%m-%d')} ({get_japanese_weekday(base_date.weekday())})
基準時刻: {base_time.strftime('%H:%M')}
生成期間: {base_date.strftime('%Y-%m-%d')} から {end_date.strftime('%Y-%m-%d')} まで
定期ルール: {rule.custom_rule}

{recent_events_history}

出力形式:
- YYYY-MM-DD形式の日付のJSONリスト
- 基準日も含める
- 日付は昇順でソート
- 例: ["2024-01-01", "2024-01-15", "2024-02-01"]

重要な注意事項:
- 過去の開催履歴から主要なパターンを分析し、そのパターンを継続してください
- 履歴にイレギュラーな開催（単発イベントや変則的な日程）が含まれる場合がありますが、それらは無視して主要なパターンのみを抽出してください
- 隔週パターンで週がずれている場合は、最も新しい開催日から隔週で開催されるようにしてください
- 「月1回」「月〇回」など月単位の周期の場合は、過去の開催曜日と週のパターンを最優先で考慮してください
- 例：過去の履歴が主に第2金曜日のパターンなら、今後も第2金曜日を選んでください
- イレギュラーな日程は生成せず、定期的なパターンのみを生成してください
- 日本の祝日を考慮する場合は、2024年以降の実際の祝日を使用
- 曜日は正確に計算すること
- 存在しない日付（2月30日など）は含めない
"""


def generate_dates_by_llm(
    rule: RecurrenceRule,
    base_date: date,
    base_time: datetime.time,
    months: int,
    llm_service: EventDateLlmService,
    recent_events_history: str,
) -> List[date]:
    """LLMを使って日付を生成する。失敗時は空リストを返す。"""
    if not rule.custom_rule:
        return []

    end_date = base_date + timedelta(days=months * 30)
    if rule.end_date and rule.end_date < end_date:
        end_date = rule.end_date

    prompt = build_llm_prompt(
        rule=rule,
        base_date=base_date,
        base_time=base_time,
        end_date=end_date,
        recent_events_history=recent_events_history,
    )

    try:
        dates = llm_service.generate_event_dates(prompt)
        return sorted({generated_date for generated_date in dates if base_date <= generated_date <= end_date})
    except Exception:
        logger.exception("LLM date generation failed")

    return []
