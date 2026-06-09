"""定期イベントの日付計算ロジック

`RecurrenceRule` の頻度設定や deterministic spec に基づき、
具体的な開催日のリストを算出する関数群を提供する。

公開関数:
    - generate_dates_by_rule(rule, base_date, months)
    - generate_dates_by_deterministic_spec(deterministic_spec, base_date, months, end_date)
    - resolve_month_dates(deterministic_spec, year, month)
    - matches_deterministic_spec(deterministic_spec, check_date)
    - get_nth_weekday_of_month(target_date, weekday, n)
    - get_week_of_month(date_obj)
    - get_japanese_weekday(weekday)
"""
from calendar import monthrange
from datetime import date, timedelta
from typing import Dict, List, Optional

from event.models import RecurrenceRule


def get_japanese_weekday(weekday: int) -> str:
    """曜日番号(0=月)を日本語の曜日表記に変換"""
    weekdays = ['月曜日', '火曜日', '水曜日', '木曜日', '金曜日', '土曜日', '日曜日']
    return weekdays[weekday]


def get_nth_weekday_of_month(target_date: date, weekday: int, n: int) -> Optional[date]:
    """指定月の第N曜日を取得する。n=-1 は最終曜日。

    対象月内に存在しない場合は None を返す。
    """
    if n == -1:
        last_day = target_date.replace(day=monthrange(target_date.year, target_date.month)[1])
        days_back = (last_day.weekday() - weekday) % 7
        return last_day - timedelta(days=days_back)

    first_day = target_date.replace(day=1)
    days_until_weekday = (weekday - first_day.weekday()) % 7
    nth_weekday = first_day + timedelta(days=days_until_weekday + (n - 1) * 7)

    if nth_weekday.month == target_date.month:
        return nth_weekday
    return None


def get_week_of_month(date_obj: date) -> int:
    """日付が「同じ曜日の第何回目」かを返す（第N週判定）"""
    count = 0
    for day in range(1, date_obj.day + 1):
        check_date = date_obj.replace(day=day)
        if check_date.weekday() == date_obj.weekday():
            count += 1
    return count


def resolve_month_dates(deterministic_spec: Dict, year: int, month: int) -> List[date]:
    """指定月における deterministic spec の開催日一覧を返す"""
    if deterministic_spec['kind'] == 'monthly_dates':
        last_day = monthrange(year, month)[1]
        days = deterministic_spec['days']
        if len(days) == 1:
            return [date(year, month, min(days[0], last_day))]
        return [date(year, month, day) for day in days if day <= last_day]

    if deterministic_spec['kind'] == 'monthly_weekdays':
        month_start = date(year, month, 1)
        resolved_dates = []
        for week, weekday in deterministic_spec['pairs']:
            resolved_date = get_nth_weekday_of_month(month_start, weekday, week)
            if resolved_date:
                resolved_dates.append(resolved_date)
        return sorted(set(resolved_dates))

    return []


def matches_deterministic_spec(deterministic_spec: Dict, check_date: date) -> bool:
    """deterministic spec に該当する日付かを判定する"""
    return check_date in resolve_month_dates(
        deterministic_spec=deterministic_spec,
        year=check_date.year,
        month=check_date.month,
    )


def generate_dates_by_deterministic_spec(
    deterministic_spec: Dict,
    base_date: date,
    months: int,
    end_date: Optional[date] = None,
) -> List[date]:
    """deterministic spec に基づき、生成期間内の日付リストを返す"""
    generated_dates = []
    generation_end = base_date + timedelta(days=months * 30)
    if end_date and end_date < generation_end:
        generation_end = end_date

    year = base_date.year
    month = base_date.month
    while True:
        current_month_dates = resolve_month_dates(
            deterministic_spec=deterministic_spec,
            year=year,
            month=month,
        )

        if current_month_dates and min(current_month_dates) > generation_end:
            break

        for current_date in current_month_dates:
            if base_date <= current_date <= generation_end:
                generated_dates.append(current_date)

        month += 1
        if month > 12:
            month = 1
            year += 1

        if date(year, month, 1) > generation_end:
            break

    return sorted(set(generated_dates))


def generate_dates_by_rule(rule: RecurrenceRule, base_date: date, months: int) -> List[date]:
    """WEEKLY / MONTHLY_BY_DATE / MONTHLY_BY_WEEK のルールから日付を生成"""
    dates = []
    end_date = base_date + timedelta(days=months * 30)

    if rule.end_date and rule.end_date < end_date:
        end_date = rule.end_date

    current_date = base_date

    if rule.frequency == 'WEEKLY':
        # 毎週または隔週
        if rule.start_date:
            # start_dateの曜日を基準に、base_date以降の最初の同じ曜日を探す
            target_weekday = rule.start_date.weekday()
            current_date = base_date

            # base_dateから最も近い同じ曜日を探す
            days_ahead = (target_weekday - current_date.weekday()) % 7
            if days_ahead == 0 and current_date < base_date:
                days_ahead = 7
            current_date = current_date + timedelta(days=days_ahead)

            # その日が開催日でない場合は、次の開催日まで進める
            while not rule.is_occurrence_date(current_date):
                current_date += timedelta(weeks=1)
        else:
            current_date = base_date

        # 開催日を収集
        while current_date <= end_date:
            if rule.is_occurrence_date(current_date):
                dates.append(current_date)
            current_date += timedelta(weeks=1)  # 1週間ずつ進めて、is_occurrence_dateで判定

    elif rule.frequency == 'MONTHLY_BY_DATE':
        # 毎月（日付指定）
        while current_date <= end_date:
            dates.append(current_date)
            # 次の月の同じ日付へ
            if current_date.month == 12:
                next_month = 1
                next_year = current_date.year + 1
            else:
                next_month = current_date.month + rule.interval
                next_year = current_date.year

            try:
                current_date = current_date.replace(year=next_year, month=next_month)
            except ValueError:
                # 月末の場合（例：1月31日→2月28日）
                current_date = current_date.replace(year=next_year, month=next_month, day=1)
                current_date = (current_date + timedelta(days=32)).replace(day=1) - timedelta(days=1)

    elif rule.frequency == 'MONTHLY_BY_WEEK':
        # 毎月（第N曜日）
        # 使用する曜日を決定（start_dateがあればその曜日、なければ基準日の曜日を使用）
        if rule.start_date:
            target_weekday = rule.start_date.weekday()
        else:
            target_weekday = current_date.weekday()

        # 最初の日付を第N曜日に調整
        first_date = get_nth_weekday_of_month(
            current_date.replace(day=1),
            target_weekday,
            rule.week_of_month or 1
        )
        if first_date and first_date >= base_date:
            current_date = first_date
        else:
            # 次の月から開始
            next_month = (current_date.month % 12) + 1
            next_year = current_date.year + (1 if next_month == 1 else 0)
            current_date = get_nth_weekday_of_month(
                date(next_year, next_month, 1),
                target_weekday,
                rule.week_of_month or 1
            )

        while current_date and current_date <= end_date:
            dates.append(current_date)
            # 次の月の第N曜日を計算
            next_month = (current_date.month % 12) + 1
            next_year = current_date.year + (1 if next_month == 1 else 0)
            next_date = get_nth_weekday_of_month(
                date(next_year, next_month, 1),
                target_weekday,
                rule.week_of_month or 1
            )
            if next_date:
                current_date = next_date
            else:
                break

    return dates
