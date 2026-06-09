"""定期イベントの日付生成サービス本体

サブモジュール (parser / calculator / llm_generator / persistence) を組み合わせ、
旧 `event.recurrence_service.RecurrenceService` と完全互換な API を提供するファサード。

公開メソッド:
    - generate_dates(rule, base_date, base_time, months=1, community=None)
    - has_deterministic_custom_rule(rule)
    - matches_custom_rule_date(rule, check_date)
    - preview_dates(...)
    - create_recurring_events(community, rule, base_date, start_time, duration, months=3)
"""
import datetime
from datetime import date
from typing import TYPE_CHECKING, Dict, List, Optional

from event.llm_service import EventDateLlmService, get_event_date_llm_service
from event.models import Event, RecurrenceRule

from event.recurrence import calculator as _calculator
from event.recurrence import llm_generator as _llm_generator
from event.recurrence import parser as _parser
from event.recurrence import persistence as _persistence

if TYPE_CHECKING:
    from community.models import Community


class RecurrenceService:
    """定期イベントの日付を生成するサービス"""

    def __init__(self, llm_service: Optional[EventDateLlmService] = None):
        self._llm_service = llm_service

    # ------------------------------------------------------------------
    # 内部: LLM サービス取得（旧 _get_llm_service と互換）
    # ------------------------------------------------------------------
    def _get_llm_service(self) -> EventDateLlmService:
        if self._llm_service is None:
            self._llm_service = get_event_date_llm_service()
        return self._llm_service

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------
    def generate_dates(
        self,
        rule: RecurrenceRule,
        base_date: date,
        base_time: datetime.time,
        months: int = 1,
        community: Optional["Community"] = None,
    ) -> List[date]:
        """定期ルールに基づいて日付リストを生成"""
        if rule.frequency in ['WEEKLY', 'MONTHLY_BY_DATE', 'MONTHLY_BY_WEEK']:
            return _calculator.generate_dates_by_rule(rule, base_date, months)
        elif rule.frequency == 'OTHER':
            deterministic_spec = _parser.parse_deterministic_custom_rule(rule)
            if deterministic_spec is not None:
                return _calculator.generate_dates_by_deterministic_spec(
                    deterministic_spec=deterministic_spec,
                    base_date=base_date,
                    months=months,
                    end_date=rule.end_date,
                )
            return self._generate_dates_by_llm(rule, base_date, base_time, months, community)
        return []

    def has_deterministic_custom_rule(self, rule: RecurrenceRule) -> bool:
        """サーバー側で安全に解釈できるカスタムルールかどうかを返す"""
        return _parser.parse_deterministic_custom_rule(rule) is not None

    def matches_custom_rule_date(self, rule: RecurrenceRule, check_date: date) -> bool:
        """カスタムルールに一致する日付かを返す"""
        deterministic_spec = _parser.parse_deterministic_custom_rule(rule)
        if deterministic_spec is None:
            return True
        return _calculator.matches_deterministic_spec(deterministic_spec, check_date)

    def preview_dates(
        self,
        frequency: str,
        custom_rule: str,
        base_date: date,
        base_time: datetime.time,
        interval: int = 1,
        week_of_month: Optional[int] = None,
        weekday: Optional[int] = None,
        months: int = 3,
        community: Optional["Community"] = None,
    ) -> Dict:
        """プレビュー用に日付リストを生成"""
        try:
            # 一時的なRecurrenceRuleオブジェクトを作成（保存はしない）
            rule = RecurrenceRule(
                frequency=frequency,
                interval=interval,
                week_of_month=week_of_month,
                custom_rule=custom_rule,
            )

            # MONTHLY_BY_WEEKの場合、weekdayからstart_dateを計算
            if frequency == 'MONTHLY_BY_WEEK' and weekday is not None and week_of_month is not None:
                start_date = _calculator.get_nth_weekday_of_month(
                    base_date.replace(day=1),
                    weekday,
                    week_of_month,
                )
                if start_date:
                    rule.start_date = start_date

            dates = self.generate_dates(rule, base_date, base_time, months, community)

            return {
                'success': True,
                'dates': [d.strftime('%Y-%m-%d') for d in dates],
                'count': len(dates),
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'dates': [],
                'count': 0,
            }

    def create_recurring_events(
        self,
        community,
        rule: RecurrenceRule,
        base_date: date,
        start_time: datetime.time,
        duration: int,
        months: int = 3,
    ) -> List[Event]:
        """定期イベントのインスタンスを作成"""
        dates = self.generate_dates(rule, base_date, start_time, months, community)
        return _persistence.create_recurring_events(
            community=community,
            rule=rule,
            dates=dates,
            start_time=start_time,
            duration=duration,
        )

    # ------------------------------------------------------------------
    # 互換用の内部メソッド（既存テストが直接叩いてくる）
    # ------------------------------------------------------------------
    def _get_recent_events_history(
        self,
        rule: RecurrenceRule,
        base_date: date,
        community: Optional["Community"] = None,
    ) -> str:
        """直近5回の開催履歴。`event.recurrence_service` ロガーで例外を吐く。"""
        return _llm_generator.get_recent_events_history(rule, base_date, community)

    def _generate_dates_by_llm(
        self,
        rule: RecurrenceRule,
        base_date: date,
        base_time: datetime.time,
        months: int,
        community: Optional["Community"] = None,
    ) -> List[date]:
        """LLMで日付生成。失敗時は空リスト。"""
        if not rule.custom_rule:
            return []

        recent_events_history = self._get_recent_events_history(rule, base_date, community)
        return _llm_generator.generate_dates_by_llm(
            rule=rule,
            base_date=base_date,
            base_time=base_time,
            months=months,
            llm_service=self._get_llm_service(),
            recent_events_history=recent_events_history,
        )
