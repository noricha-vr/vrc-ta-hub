"""定期イベントの日付生成サービス"""
import json
from datetime import datetime, timedelta, date
from typing import List, Optional, Dict
import re
import traceback

from django.conf import settings
from django.utils import timezone
from django.db import models
import google.generativeai as genai

from event.models import Event, RecurrenceRule


class RecurrenceService:
    """定期イベントの日付を生成するサービス"""
    
    def __init__(self):
        genai.configure(api_key=settings.GOOGLE_API_KEY)
        model_name = getattr(settings, 'GEMINI_MODEL', 'gemini-2.0-flash-exp')
        self.model = genai.GenerativeModel(model_name)
    
    def generate_dates(self, rule: RecurrenceRule, base_date: date, base_time: datetime.time, months: int = 1, community=None) -> List[date]:
        """定期ルールに基づいて日付リストを生成"""
        if rule.frequency in ['WEEKLY', 'MONTHLY_BY_DATE', 'MONTHLY_BY_WEEK']:
            # ルールベースで生成
            return self._generate_dates_by_rule(rule, base_date, months)
        elif rule.frequency == 'OTHER':
            # LLMで生成
            return self._generate_dates_by_llm(rule, base_date, base_time, months, community)
        return []
    
    def _generate_dates_by_rule(self, rule: RecurrenceRule, base_date: date, months: int) -> List[date]:
        """ルールベースで日付リストを生成"""
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
            # 使用する曜日を決定（weekdayが指定されていればそれを使用、なければ基準日の曜日）
            target_weekday = rule.weekday if rule.weekday is not None else current_date.weekday()
            
            # 最初の日付を第N曜日に調整
            first_date = self._get_nth_weekday_of_month(
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
                current_date = self._get_nth_weekday_of_month(
                    date(next_year, next_month, 1),
                    target_weekday,
                    rule.week_of_month or 1
                )
            
            while current_date and current_date <= end_date:
                dates.append(current_date)
                # 次の月の第N曜日を計算
                next_month = (current_date.month % 12) + 1
                next_year = current_date.year + (1 if next_month == 1 else 0)
                next_date = self._get_nth_weekday_of_month(
                    date(next_year, next_month, 1),
                    target_weekday,
                    rule.week_of_month or 1
                )
                if next_date:
                    current_date = next_date
                else:
                    break
        
        return dates
    
    def _get_japanese_weekday(self, weekday: int) -> str:
        """曜日番号を日本語の曜日に変換"""
        weekdays = ['月曜日', '火曜日', '水曜日', '木曜日', '金曜日', '土曜日', '日曜日']
        return weekdays[weekday]
    
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
    
    def _get_week_of_month(self, date_obj: date) -> int:
        """日付が月の第何週かを取得（その曜日の第何回目の出現か）"""
        # 同じ曜日の第何回目かを計算
        # 例: 2024-11-25（月曜日）は11月の第4月曜日
        count = 0
        for day in range(1, date_obj.day + 1):
            check_date = date_obj.replace(day=day)
            if check_date.weekday() == date_obj.weekday():
                count += 1
        return count
    
    def _get_recent_events_history(self, rule: RecurrenceRule, base_date: date, community=None) -> str:
        """直近5回の開催履歴を取得してフォーマット"""
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
                weekday = self._get_japanese_weekday(event.date.weekday())
                week = self._get_week_of_month(event.date)
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
                    wd_name = self._get_japanese_weekday(wd)
                    weekday_counts[wd_name] = weekday_counts.get(wd_name, 0) + 1
                
                if weekday_counts:
                    most_common_weekday = max(weekday_counts, key=weekday_counts.get)
                    history_lines.append(f"- 主な開催曜日: {most_common_weekday}")
                
                # 週の傾向（第何週か）
                weeks = [self._get_week_of_month(e.date) for e in recent_events]
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
            
        except Exception as e:
            print(f"Error getting recent events history: {e}")
            return "過去の開催履歴: 取得エラー"
    
    def _generate_dates_by_llm(self, rule: RecurrenceRule, base_date: date, base_time: datetime.time, months: int, community=None) -> List[date]:
        """LLMを使用して日付リストを生成"""
        if not rule.custom_rule:
            return []
        
        end_date = base_date + timedelta(days=months * 30)
        if rule.end_date and rule.end_date < end_date:
            end_date = rule.end_date
        
        # 直近5回の開催履歴を取得
        recent_events_history = self._get_recent_events_history(rule, base_date, community)
        
        prompt = f"""
以下の条件で定期イベントの日付リストを生成してください。

基準日: {base_date.strftime('%Y-%m-%d')} ({self._get_japanese_weekday(base_date.weekday())})
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
                     interval: int = 1, week_of_month: Optional[int] = None, weekday: Optional[int] = None, 
                     months: int = 3, community=None) -> Dict:
        """プレビュー用に日付リストを生成"""
        try:
            # 一時的なRecurrenceRuleオブジェクトを作成（保存はしない）
            rule = RecurrenceRule(
                frequency=frequency,
                interval=interval,
                week_of_month=week_of_month,
                custom_rule=custom_rule
            )
            
            # weekdayは一時的に属性として追加（MONTHLY_BY_WEEKの場合のみ使用）
            if weekday is not None:
                rule.weekday = weekday
            
            dates = self.generate_dates(rule, base_date, base_time, months, community)
            
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
        dates = self.generate_dates(rule, base_date, start_time, months, community)
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
