"""定期イベントの日付生成サービス"""
import json
from datetime import datetime, timedelta, date
from typing import List, Optional, Dict
import re
import traceback

from django.conf import settings
from django.utils import timezone
import google.generativeai as genai

from event.models import Event, RecurrenceRule


class RecurrenceService:
    """定期イベントの日付を生成するサービス"""
    
    def __init__(self):
        genai.configure(api_key=settings.GOOGLE_API_KEY)
        self.model = genai.GenerativeModel('gemini-2.0-flash-thinking')
    
    def generate_dates(self, rule: RecurrenceRule, base_date: date, base_time: datetime.time, months: int = 3) -> List[date]:
        """定期ルールに基づいて日付リストを生成"""
        if rule.frequency in ['WEEKLY', 'MONTHLY_BY_DATE', 'MONTHLY_BY_WEEK']:
            # ルールベースで生成
            return self._generate_dates_by_rule(rule, base_date, months)
        elif rule.frequency == 'OTHER':
            # LLMで生成
            return self._generate_dates_by_llm(rule, base_date, base_time, months)
        return []
    
    def _generate_dates_by_rule(self, rule: RecurrenceRule, base_date: date, months: int) -> List[date]:
        """ルールベースで日付リストを生成"""
        dates = []
        end_date = base_date + timedelta(days=months * 30)
        
        if rule.end_date and rule.end_date < end_date:
            end_date = rule.end_date
        
        current_date = base_date
        
        if rule.frequency == 'WEEKLY':
            # 毎週
            while current_date <= end_date:
                dates.append(current_date)
                current_date += timedelta(weeks=rule.interval)
        
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
            while current_date <= end_date:
                dates.append(current_date)
                # 次の月の第N曜日を計算
                next_date = self._get_nth_weekday_of_month(
                    current_date + timedelta(days=32),
                    current_date.weekday(),
                    rule.week_of_month or 1
                )
                if next_date:
                    current_date = next_date
                else:
                    break
        
        return dates
    
    def _get_nth_weekday_of_month(self, target_date: date, weekday: int, n: int) -> Optional[date]:
        """指定月の第N曜日を取得"""
        # 月初の日付を取得
        first_day = target_date.replace(day=1)
        
        # 月初から最初の指定曜日までの日数
        days_until_weekday = (weekday - first_day.weekday()) % 7
        
        # 第N曜日の日付を計算
        nth_weekday = first_day + timedelta(days=days_until_weekday + (n - 1) * 7)
        
        # 同じ月内かチェック
        if nth_weekday.month == target_date.month:
            return nth_weekday
        return None
    
    def _generate_dates_by_llm(self, rule: RecurrenceRule, base_date: date, base_time: datetime.time, months: int) -> List[date]:
        """LLMを使用して日付リストを生成"""
        if not rule.custom_rule:
            return []
        
        end_date = base_date + timedelta(days=months * 30)
        if rule.end_date and rule.end_date < end_date:
            end_date = rule.end_date
        
        prompt = f"""
以下の条件で定期イベントの日付リストを生成してください。

基準日: {base_date.strftime('%Y-%m-%d')}
基準時刻: {base_time.strftime('%H:%M')}
生成期間: {base_date.strftime('%Y-%m-%d')} から {end_date.strftime('%Y-%m-%d')} まで
定期ルール: {rule.custom_rule}

出力形式:
- YYYY-MM-DD形式の日付のJSONリスト
- 基準日も含める
- 日付は昇順でソート
- 例: ["2024-01-01", "2024-01-15", "2024-02-01"]

注意事項:
- 日本の祝日を考慮する場合は、2024年以降の実際の祝日を使用
- 曜日は正確に計算すること
- 存在しない日付（2月30日など）は含めない
"""
        
        try:
            response = self.model.generate_content(prompt)
            # JSONを抽出
            text = response.text
            
            # JSONの開始と終了を見つける
            json_start = text.find('[')
            json_end = text.rfind(']') + 1
            
            if json_start != -1 and json_end > json_start:
                json_str = text[json_start:json_end]
                date_strings = json.loads(json_str)
                
                # 文字列を日付オブジェクトに変換
                dates = []
                for date_str in date_strings:
                    try:
                        parsed_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                        if base_date <= parsed_date <= end_date:
                            dates.append(parsed_date)
                    except ValueError:
                        continue
                
                return sorted(dates)
        except Exception as e:
            print(f"LLM date generation error: {e}")
            traceback.print_exc()
        
        return []
    
    def preview_dates(self, frequency: str, custom_rule: str, base_date: date, base_time: datetime.time, 
                     interval: int = 1, week_of_month: Optional[int] = None, months: int = 3) -> Dict:
        """プレビュー用に日付リストを生成"""
        try:
            # 一時的なRecurrenceRuleオブジェクトを作成（保存はしない）
            rule = RecurrenceRule(
                frequency=frequency,
                interval=interval,
                week_of_month=week_of_month,
                custom_rule=custom_rule
            )
            
            dates = self.generate_dates(rule, base_date, base_time, months)
            
            return {
                'success': True,
                'dates': [d.strftime('%Y-%m-%d') for d in dates],
                'count': len(dates)
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'dates': [],
                'count': 0
            }
    
    def create_recurring_events(self, community, rule: RecurrenceRule, base_date: date, 
                              start_time: datetime.time, duration: int, months: int = 3) -> List[Event]:
        """定期イベントのインスタンスを作成"""
        dates = self.generate_dates(rule, base_date, start_time, months)
        created_events = []
        
        # マスターイベントを作成（最初の日付）
        if dates:
            master_event = Event.objects.create(
                community=community,
                date=dates[0],
                start_time=start_time,
                duration=duration,
                weekday=dates[0].strftime('%a').upper()[:3],
                recurrence_rule=rule,
                is_recurring_master=True
            )
            created_events.append(master_event)
            
            # 残りのインスタンスを作成
            for event_date in dates[1:]:
                # 既存のイベントがあるかチェック
                existing = Event.objects.filter(
                    community=community,
                    date=event_date,
                    start_time=start_time
                ).first()
                
                if not existing:
                    event = Event.objects.create(
                        community=community,
                        date=event_date,
                        start_time=start_time,
                        duration=duration,
                        weekday=event_date.strftime('%a').upper()[:3],
                        recurring_master=master_event
                    )
                    created_events.append(event)
        
        return created_events