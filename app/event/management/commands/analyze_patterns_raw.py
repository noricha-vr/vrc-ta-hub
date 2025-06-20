"""既存イベントの定期パターンを分析（生SQL版）"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = '既存イベントの定期パターンを分析（生SQL版）'

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            # 定期パターンの可能性があるイベントを取得
            cursor.execute("""
                SELECT 
                    c.name as community_name,
                    e.start_time,
                    e.weekday,
                    e.duration,
                    COUNT(*) as event_count,
                    GROUP_CONCAT(e.date ORDER BY e.date SEPARATOR '|') as dates
                FROM event e
                JOIN community c ON e.community_id = c.id
                WHERE c.status = 'approved'
                GROUP BY c.id, e.start_time, e.weekday, e.duration
                HAVING COUNT(*) >= 3
                ORDER BY c.name, COUNT(*) DESC
                LIMIT 50
            """)
            
            results = cursor.fetchall()
            
            self.stdout.write('=== 定期パターンの可能性があるイベント ===\n')
            
            total_patterns = 0
            total_events = 0
            
            for row in results:
                community_name, start_time, weekday, duration, event_count, dates_str = row
                dates = dates_str.split('|') if dates_str else []
                
                # 最初の5つの日付を表示
                date_preview = dates[:5]
                if len(dates) > 5:
                    date_preview.append(f'... 他{len(dates) - 5}件')
                
                self.stdout.write(
                    f'{community_name}: {start_time} {weekday} ({duration}分) - {event_count}回'
                )
                for date in date_preview:
                    self.stdout.write(f'  - {date}')
                self.stdout.write('')
                
                total_patterns += 1
                total_events += event_count
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n合計: {total_patterns}個の定期パターン、{total_events}件のイベント'
                )
            )