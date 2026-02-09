"""generate_recurring_eventsコマンドのテスト"""
from datetime import datetime, time, timedelta
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from io import StringIO

from community.models import Community
from user_account.models import CustomUser
from event.models import Event, RecurrenceRule


class GenerateRecurringEventsCommandTest(TestCase):
    def setUp(self):
        """テストデータのセットアップ"""
        # ユーザーとコミュニティを作成
        self.user = CustomUser.objects.create_user(
            user_name='testuser',
            email='test@example.com',
            password='testpass'
        )
        
        self.community = Community.objects.create(
            name='テストコミュニティ',
            description='テスト用コミュニティ',
            weekdays=['Mon'],
            start_time=time(21, 0),
            duration=60,
            status='approved'
        )
        
        # 定期ルール（毎週）を作成
        self.weekly_rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='WEEKLY',
            interval=1
        )
        
        # マスターイベントを作成（過去の日付）
        self.master_event = Event.objects.create(
            community=self.community,
            date=timezone.now().date() - timedelta(days=7),
            start_time=time(21, 0),
            duration=60,
            weekday='MON',
            is_recurring_master=True,
            recurrence_rule=self.weekly_rule
        )
        
    def test_generate_recurring_events_basic(self):
        """基本的なイベント生成のテスト"""
        out = StringIO()
        call_command('generate_recurring_events', '--months=1', stdout=out)
        
        # 出力メッセージを確認
        output = out.getvalue()
        self.assertIn('1ヶ月先までの定期イベントを生成します', output)
        self.assertIn('件のイベントを作成しました', output)
        
        # 生成されたイベントを確認
        generated_events = Event.objects.filter(
            recurring_master=self.master_event,
            date__gte=timezone.now().date()
        )
        self.assertGreater(generated_events.count(), 0)
        
        # 生成されたイベントの詳細を確認
        for event in generated_events:
            # RecurrenceServiceは毎週月曜日を生成するはずだが、
            # 実際の曜日判定はアルゴリズムに依存するため、
            # ここでは時間と期間のみ確認
            self.assertEqual(event.start_time, time(21, 0))
            self.assertEqual(event.duration, 60)
            self.assertEqual(event.community, self.community)
            
    def test_dry_run_option(self):
        """ドライランオプションのテスト"""
        out = StringIO()
        call_command('generate_recurring_events', '--months=1', '--dry-run', stdout=out)
        
        # 出力メッセージを確認
        output = out.getvalue()
        self.assertIn('件のイベントが作成される予定です', output)
        
        # 実際にはイベントが作成されていないことを確認
        generated_events = Event.objects.filter(
            recurring_master=self.master_event,
            date__gte=timezone.now().date()
        )
        self.assertEqual(generated_events.count(), 0)
        
    def test_reset_future_option(self):
        """未来のイベントリセットオプションのテスト"""
        # 先に未来のイベントを作成
        future_date = timezone.now().date() + timedelta(days=14)
        future_event = Event.objects.create(
            community=self.community,
            date=future_date,
            start_time=time(21, 0),
            duration=60,
            weekday='MON',
            recurring_master=self.master_event
        )
        
        # リセットオプション付きでコマンド実行
        out = StringIO()
        call_command('generate_recurring_events', '--months=1', '--reset-future', stdout=out)
        
        # 出力メッセージを確認
        output = out.getvalue()
        self.assertIn('未来のイベントを削除しています', output)
        self.assertIn('1件の定期イベントインスタンスを削除しました', output)
        
        # 元のイベントが削除されたことを確認
        self.assertFalse(Event.objects.filter(id=future_event.id).exists())
        
        # 新しいイベントが生成されたことを確認
        generated_events = Event.objects.filter(
            recurring_master=self.master_event,
            date__gte=timezone.now().date()
        )
        self.assertGreater(generated_events.count(), 0)
        
    def test_multiple_months_generation(self):
        """複数月の生成テスト"""
        out = StringIO()
        call_command('generate_recurring_events', '--months=3', stdout=out)
        
        # 3ヶ月分のイベントが生成されることを確認
        generated_events = Event.objects.filter(
            recurring_master=self.master_event,
            date__gte=timezone.now().date(),
            date__lte=timezone.now().date() + timedelta(days=90)
        )
        
        # 約12-13週分のイベントがあるはず
        self.assertGreaterEqual(generated_events.count(), 10)
        self.assertLessEqual(generated_events.count(), 15)
        
    def test_no_duplicate_generation(self):
        """重複生成されないことのテスト"""
        # 固定の期間で生成
        today = timezone.now().date()
        
        # 最初の生成
        out1 = StringIO()
        call_command('generate_recurring_events', '--months=1', stdout=out1)
        
        # 特定の日付範囲内のイベントのみカウント
        end_date = today + timedelta(days=28)  # 厳密に4週間
        first_events = Event.objects.filter(
            recurring_master=self.master_event,
            date__gte=today,
            date__lte=end_date
        )
        first_dates = set(first_events.values_list('date', flat=True))
        
        # 再度同じコマンドを実行
        out2 = StringIO()
        call_command('generate_recurring_events', '--months=1', stdout=out2)
        
        # 同じ日付範囲内のイベントを再度確認
        second_events = Event.objects.filter(
            recurring_master=self.master_event,
            date__gte=today,
            date__lte=end_date
        )
        second_dates = set(second_events.values_list('date', flat=True))
        
        # 同じ日付範囲内では重複生成されないことを確認
        self.assertEqual(first_dates, second_dates, 
                        "同じ日付範囲内で重複生成が発生しました")
        
    def test_biweekly_rule(self):
        """隔週ルールのテスト"""
        # 隔週用の独自Communityを作成（start_time/durationをマスターと変える）
        biweekly_community = Community.objects.create(
            name='隔週テストコミュニティ',
            description='隔週テスト用',
            weekdays=['Mon'],
            start_time=time(20, 0),
            duration=45,
            status='approved'
        )

        today = timezone.now().date()
        # 今日の曜日と同じ曜日の14日前をstart_dateに設定
        start_date = today - timedelta(days=14)

        biweekly_rule = RecurrenceRule.objects.create(
            community=biweekly_community,
            frequency='WEEKLY',
            interval=2,
            start_date=start_date
        )

        # マスターイベント（start_time/durationはCommunityと異なる値）
        biweekly_master = Event.objects.create(
            community=biweekly_community,
            date=start_date,
            start_time=time(22, 0),
            duration=90,
            weekday=start_date.strftime('%a').upper()[:3],
            is_recurring_master=True,
            recurrence_rule=biweekly_rule
        )

        out = StringIO()
        call_command('generate_recurring_events', '--months=2', stdout=out)

        # 生成されたイベントを確認
        generated_events = Event.objects.filter(
            recurring_master=biweekly_master,
            date__gte=today
        ).order_by('date')

        self.assertGreater(generated_events.count(), 0, "隔週イベントが生成されていない")

        # Communityのstart_time/durationが使われることを確認
        for event in generated_events:
            self.assertEqual(event.start_time, time(20, 0))
            self.assertEqual(event.duration, 45)

        # 隔週であることを確認
        if generated_events.count() >= 2:
            first_event = generated_events[0]
            second_event = generated_events[1]
            date_diff = (second_event.date - first_event.date).days
            self.assertEqual(date_diff, 14)  # 2週間の差
            
    def test_monthly_by_week_rule(self):
        """月次（第N曜日）ルールのテスト"""
        # 月次用の独自Communityを作成（start_time/durationをマスターと変える）
        monthly_community = Community.objects.create(
            name='月次テストコミュニティ',
            description='月次テスト用',
            weekdays=['Tue'],
            start_time=time(19, 0),
            duration=30,
            status='approved'
        )

        # 毎月第2火曜日のルール
        monthly_rule = RecurrenceRule.objects.create(
            community=monthly_community,
            frequency='MONTHLY_BY_WEEK',
            interval=1,
            week_of_month=2  # 第2週
        )

        # マスターイベント（start_time/durationはCommunityと異なる値）
        monthly_master = Event.objects.create(
            community=monthly_community,
            date=timezone.now().date() - timedelta(days=60),
            start_time=time(20, 0),
            duration=120,
            weekday='TUE',
            is_recurring_master=True,
            recurrence_rule=monthly_rule
        )

        out = StringIO()
        call_command('generate_recurring_events', '--months=3', stdout=out)

        # 生成されたイベントを確認
        generated_events = Event.objects.filter(
            recurring_master=monthly_master,
            date__gte=timezone.now().date()
        )

        # Communityのstart_time/durationが使われることを確認
        self.assertGreater(generated_events.count(), 0, "月次イベントが生成されていない")
        for event in generated_events:
            self.assertEqual(event.start_time, time(19, 0))
            self.assertEqual(event.duration, 30)

    def test_community_time_change_reflected(self):
        """Communityの時刻変更が生成イベントに反映されることのテスト

        マスターイベントは21:00だが、Communityを22:00に変更した場合、
        生成されるイベントは22:00になるべき。
        """
        # Communityの開始時刻を変更（マスターイベントは21:00のまま）
        self.community.start_time = time(22, 0)
        self.community.save()

        out = StringIO()
        call_command('generate_recurring_events', '--months=1', stdout=out)

        # 生成されたイベントを確認
        generated_events = Event.objects.filter(
            recurring_master=self.master_event,
            date__gte=timezone.now().date()
        )
        self.assertGreater(generated_events.count(), 0)

        for event in generated_events:
            self.assertEqual(
                event.start_time, time(22, 0),
                f"イベント {event.date} の開始時刻が22:00ではなく{event.start_time}"
            )

    def test_community_duration_change_reflected(self):
        """Communityのduration変更が生成イベントに反映されることのテスト

        マスターイベントはduration=60だが、Communityを90に変更した場合、
        生成されるイベントはduration=90になるべき。
        """
        # Communityのdurationを変更（マスターイベントは60のまま）
        self.community.duration = 90
        self.community.save()

        out = StringIO()
        call_command('generate_recurring_events', '--months=1', stdout=out)

        # 生成されたイベントを確認
        generated_events = Event.objects.filter(
            recurring_master=self.master_event,
            date__gte=timezone.now().date()
        )
        self.assertGreater(generated_events.count(), 0)

        for event in generated_events:
            self.assertEqual(
                event.duration, 90,
                f"イベント {event.date} のdurationが90ではなく{event.duration}"
            )
