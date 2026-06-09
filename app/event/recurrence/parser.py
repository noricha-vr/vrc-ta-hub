"""カスタムルール（自由記述）のパーサ

`RecurrenceRule.custom_rule` の自由記述文字列から、
deterministic に解釈できる仕様を抽出するロジックを提供する。

公開関数:
    - parse_deterministic_custom_rule(rule): メインのエントリポイント
    - normalize_custom_rule(custom_rule): 正規化
    - extract_monthly_dates(normalized_rule): 日付指定の抽出
    - extract_monthly_weekday_rules(normalized_rule): 第N曜日指定の抽出
"""
import re
from typing import Dict, List, Optional

from event.models import RecurrenceRule

from event.recurrence.calculator import get_week_of_month

WEEKDAY_TOKEN_MAP = {
    '月': 0,
    '火': 1,
    '水': 2,
    '木': 3,
    '金': 4,
    '土': 5,
    '日': 6,
}

WEEK_TOKEN_MAP = {
    '1': 1,
    '2': 2,
    '3': 3,
    '4': 4,
    '5': 5,
    '一': 1,
    '二': 2,
    '三': 3,
    '四': 4,
    '五': 5,
    '最終': -1,
}


def normalize_custom_rule(custom_rule: Optional[str]) -> str:
    """全角数字を半角化し、空白を取り除いた文字列を返す"""
    if not custom_rule:
        return ''

    normalized_rule = custom_rule.translate(str.maketrans('０１２３４５６７８９', '0123456789'))
    return re.sub(r'\s+', '', normalized_rule)


def extract_monthly_dates(normalized_rule: str) -> List[int]:
    """日付指定の自由記述ルールから日付リスト(1-31)を抽出する"""
    single_day_match = re.fullmatch(r'毎月(\d{1,2})日(?:開催)?', normalized_rule)
    if single_day_match:
        return [int(single_day_match.group(1))]

    date_tokens = [
        int(token)
        for token in re.findall(r'(?<!第)(\d{1,2})日', normalized_rule)
    ]
    valid_dates = sorted({token for token in date_tokens if 1 <= token <= 31})
    if len(valid_dates) >= 2:
        return valid_dates
    return []


def extract_monthly_weekday_rules(normalized_rule: str) -> List[tuple[int, int]]:
    """第N曜日系の自由記述ルールから (week, weekday) のリストを抽出する"""
    pairs: list[tuple[int, int]] = []

    for segment in re.split(r'[、,]', normalized_rule):
        cleaned_segment = segment.split('：')[-1]
        cleaned_segment = cleaned_segment.strip('（）()')

        last_match = re.search(r'最終([月火水木金土日])曜日', cleaned_segment)
        if last_match:
            pairs.append((-1, WEEKDAY_TOKEN_MAP[last_match.group(1)]))
            continue

        week_day_match = re.search(
            r'(第?(?:[1-5一二三四五]|最終)(?:・第?(?:[1-5一二三四五]|最終))*)([月火水木金土日])曜日',
            cleaned_segment,
        )
        if week_day_match:
            weekday = WEEKDAY_TOKEN_MAP[week_day_match.group(2)]
            for week_token in week_day_match.group(1).split('・'):
                normalized_week = week_token.replace('第', '')
                week = WEEK_TOKEN_MAP.get(normalized_week)
                if week is not None:
                    pairs.append((week, weekday))

    return sorted(set(pairs))


def parse_deterministic_custom_rule(rule: RecurrenceRule) -> Optional[Dict]:
    """既知の自由記述ルールを deterministic に解釈する。

    解釈できないルールは None を返し、LLM 生成へフォールバックさせる。
    """
    normalized_rule = normalize_custom_rule(rule.custom_rule)
    if not normalized_rule:
        return None

    monthly_dates = extract_monthly_dates(normalized_rule)
    if monthly_dates:
        return {
            'kind': 'monthly_dates',
            'days': monthly_dates,
        }

    monthly_weekdays = extract_monthly_weekday_rules(normalized_rule)
    if monthly_weekdays:
        return {
            'kind': 'monthly_weekdays',
            'pairs': monthly_weekdays,
        }

    if normalized_rule in {'毎月', '月1'} and rule.start_date:
        return {
            'kind': 'monthly_weekdays',
            'pairs': [(get_week_of_month(rule.start_date), rule.start_date.weekday())],
        }

    return None
