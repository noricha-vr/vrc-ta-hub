"""マイページの Vket バナーの節目・日数・表示期間のテスト。"""
from datetime import date, datetime, time, timezone as datetime_timezone
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tests.factories import make_community, make_user
from vket.models import VketCollaboration, VketParticipation


class VketBannerMilestoneTests(TestCase):
    """選択中の集会にとって最も近い節目を表示する。"""

    def setUp(self):
        self.user = make_user()
        self.community = make_community(owner=self.user)
        self.collaboration = VketCollaboration.objects.create(
            slug='winter-banner',
            name='Vket 2026 Winter 技術学術WEEK',
            phase=VketCollaboration.Phase.ENTRY_OPEN,
            registration_deadline=date(2026, 11, 1),
            lt_deadline=date(2026, 11, 23),
            period_start=date(2026, 12, 5),
            period_end=date(2026, 12, 20),
            settings_json={
                'schedule_milestones': {
                    'kickoff': {
                        'date': '2026-11-30', 'label': '説明会・キックオフ',
                        'tentative': True, 'time': '21:00',
                    },
                    'after_party': {
                        'date': '2026-12-25', 'label': 'お疲れ様でした会',
                        'tentative': True, 'time': '21:00',
                    },
                },
            },
        )
        self.client.force_login(self.user)

    def _get_response(self, today):
        """日付を固定してマイページを開く。"""
        with patch('event.views.my_list.timezone.localdate', return_value=today):
            response = self.client.get(reverse('event:my_list'))
        self.assertEqual(response.status_code, 200)
        return response

    def _apply(self, **kwargs):
        """申し込み済みの参加と希望日程を用意する。"""
        return VketParticipation.objects.create(
            collaboration=self.collaboration, community=self.community,
            progress=VketParticipation.Progress.APPLIED,
            requested_date=date(2026, 12, 10), requested_start_time=time(22, 0),
            **kwargs,
        )

    def test_unapplied_community_sees_registration_deadline(self):
        """参加がない場合も未申請の参加がある場合も参加表明の締切を選ぶ。"""
        for has_participation in (False, True):
            with self.subTest(has_participation=has_participation):
                if has_participation:
                    VketParticipation.objects.create(
                        collaboration=self.collaboration, community=self.community,
                    )
                response = self._get_response(date(2026, 10, 10))
                banner = response.context['vket_banner']
                self.assertEqual(banner['milestone']['key'], 'registration_deadline')
                self.assertEqual(banner['days_until'], 22)
                self.assertEqual(banner['date_display'], '11/1（日）')
                self.assertContains(response, 'あと22日')
                self.assertContains(response, '参加表明の締切')
                self.assertContains(response, 'Vket 2026 Winter 技術学術WEEK 12/5〜12/20')
                self.assertNotContains(response, 'role="status"')

    def test_applied_community_sees_next_milestone_in_order(self):
        """申し込み済みなら発表締切・説明会・自分の開催日・お疲れ様会へ進む。"""
        self._apply()
        for today, key, days in (
            (date(2026, 10, 10), 'lt_deadline', 44),
            (date(2026, 11, 24), 'kickoff', 6),
            (date(2026, 12, 1), 'community_event', 9),
            (date(2026, 12, 11), 'after_party', 14),
        ):
            with self.subTest(key=key):
                banner = self._get_response(today).context['vket_banner']
                self.assertEqual(banner['milestone']['key'], key)
                self.assertEqual(banner['days_until'], days)

    def test_selection_uses_dates_instead_of_fixed_priority(self):
        """設定で説明会が発表締切より早ければ説明会を選ぶ。"""
        self._apply()
        self.collaboration.settings_json['schedule_milestones']['kickoff']['date'] = '2026-11-10'
        self.collaboration.save(update_fields=['settings_json'])
        banner = self._get_response(date(2026, 11, 2)).context['vket_banner']
        self.assertEqual(banner['milestone']['key'], 'kickoff')

    def test_same_day_milestones_use_specification_order(self):
        """同日に発表締切と説明会があれば締切を優先する。"""
        self._apply()
        self.collaboration.settings_json['schedule_milestones']['kickoff']['date'] = '2026-11-23'
        self.collaboration.save(update_fields=['settings_json'])
        banner = self._get_response(date(2026, 11, 23)).context['vket_banner']
        self.assertEqual(banner['milestone']['key'], 'lt_deadline')
        self.assertEqual(banner['days_until'], 0)

    def test_deadline_today(self):
        """締切当日はゼロ日ではなく「今日」と「締切です」を表示する。"""
        response = self._get_response(date(2026, 11, 1))
        banner = response.context['vket_banner']
        self.assertEqual(banner['message'], '参加表明の締切です')
        self.assertEqual(banner['days_until'], 0)
        self.assertContains(response, '今日')
        self.assertNotContains(response, 'あと0日')

    def test_event_today_includes_start_time(self):
        """開催当日は開始時刻と自分の集会の開催日を表示する。"""
        self._apply()
        response = self._get_response(date(2026, 12, 10))
        banner = response.context['vket_banner']
        self.assertEqual(banner['days_until'], 0)
        self.assertEqual(banner['message'], '22:00 から あなたの集会の開催日です')
        self.assertContains(response, '今日')
        self.assertContains(response, '22:00 から あなたの集会の開催日です')

    def test_event_uses_confirmed_schedule_before_requested_schedule(self):
        """確定日・開始時刻があれば希望日程より優先する。"""
        self._apply(confirmed_date=date(2026, 12, 12), confirmed_start_time=time(20, 30))
        banner = self._get_response(date(2026, 12, 1)).context['vket_banner']
        self.assertEqual(banner['days_until'], 11)
        self.assertEqual(banner['date_display'], '12/12（土）')
        self.assertEqual(banner['time_display'], '20:30')

    def test_missing_community_date_skips_to_after_party(self):
        """希望日も確定日もない参加では自分の開催日を候補にしない。"""
        participation = self._apply()
        participation.requested_date = None
        participation.save(update_fields=['requested_date'])
        banner = self._get_response(date(2026, 12, 1)).context['vket_banner']
        self.assertEqual(banner['milestone']['key'], 'after_party')

    def test_unapplied_after_deadline_only_shows_open_registration(self):
        """受付中でも過ぎた締切や申込済み向けの節目は出さない。"""
        for progress in (None, VketParticipation.Progress.NOT_APPLIED):
            with self.subTest(progress=progress):
                if progress:
                    VketParticipation.objects.create(
                        collaboration=self.collaboration, community=self.community,
                        progress=progress,
                    )
                response = self._get_response(date(2026, 11, 2))
                banner = response.context['vket_banner']
                self.assertEqual(banner['message'], '参加申し込み受付中')
                self.assertIsNone(banner['days_until'])
                self.assertIsNone(banner['milestone'])
                self.assertNotContains(response, '参加表明の締切')
                self.assertNotContains(response, '発表者・テーマの登録締切')

    def test_unapplied_after_deadline_when_registration_closed(self):
        """受付が閉じた未申込の集会には過ぎた締切も受付中の文言も出さない。"""
        self.collaboration.phase = VketCollaboration.Phase.SCHEDULING
        self.collaboration.save(update_fields=['phase'])
        response = self._get_response(date(2026, 11, 2))
        self.assertIsNone(response.context['vket_banner']['milestone'])
        self.assertNotContains(response, '参加申し込み受付中')
        self.assertNotContains(response, '参加表明の締切')

    def test_admin_without_community_sees_global_milestones(self):
        """所属集会がない管理者はコラボ全体の次の節目と管理リンクを使う。"""
        admin = make_user(user_name='banner_admin', email='admin@example.com', is_staff=True)
        self.client.force_login(admin)
        for today, key in (
            (date(2026, 10, 10), 'registration_deadline'),
            (date(2026, 11, 2), 'lt_deadline'),
            (date(2026, 11, 24), 'kickoff'),
            (date(2026, 12, 1), 'period_start'),
            (date(2026, 12, 6), 'after_party'),
        ):
            with self.subTest(key=key):
                response = self._get_response(today)
                banner = response.context['vket_banner']
                self.assertEqual(banner['milestone']['key'], key)
                self.assertContains(response, '管理画面を開く')
                self.assertNotContains(response, '参加状況を確認')

    def test_active_community_determines_milestone(self):
        """別の集会が申込済みでも選択中の未申込集会には参加表明締切を出す。"""
        self._apply()
        other = make_community(name='Other Community', owner=self.user)
        session = self.client.session
        session['active_community_id'] = other.pk
        session.save()
        banner = self._get_response(date(2026, 10, 10)).context['vket_banner']
        self.assertEqual(banner['milestone']['key'], 'registration_deadline')

    def test_tentative_date_and_time(self):
        """暫定日時には「頃」と暫定印を表示する。"""
        self._apply()
        response = self._get_response(date(2026, 11, 24))
        banner = response.context['vket_banner']
        self.assertEqual(banner['date_display'], '11/30（月）頃')
        self.assertEqual(banner['time_display'], '21時頃')
        self.assertContains(response, '11/30（月）頃')
        self.assertContains(response, '21時頃')
        self.assertContains(response, '暫定')
        banner = self._get_response(date(2026, 11, 30)).context['vket_banner']
        self.assertEqual(banner['message'], '21時頃 から 説明会・キックオフです')

    def test_exact_milestone_and_tentative_minutes(self):
        """暫定でない日時は通常の時刻、暫定で分があれば分まで表示する。"""
        self._apply()
        kickoff = self.collaboration.settings_json['schedule_milestones']['kickoff']
        for tentative, expected_time in ((False, '21:30'), (True, '21時30分頃')):
            with self.subTest(tentative=tentative):
                kickoff.update(tentative=tentative, time='21:30')
                self.collaboration.save(update_fields=['settings_json'])
                banner = self._get_response(date(2026, 11, 24)).context['vket_banner']
                self.assertEqual(banner['time_display'], expected_time)
                self.assertEqual(banner['date_display'].endswith('頃'), tentative)

    def test_banner_remains_through_after_party_and_then_disappears(self):
        """会期が終わってもお疲れ様会の当日まで表示し、翌日は消える。"""
        self._apply()
        for today, days in ((date(2026, 12, 21), 4), (date(2026, 12, 25), 0)):
            with self.subTest(today=today):
                banner = self._get_response(today).context['vket_banner']
                self.assertEqual(banner['milestone']['key'], 'after_party')
                self.assertEqual(banner['days_until'], days)
        self.assertIsNone(self._get_response(date(2026, 12, 26)).context['vket_banner'])

    def test_earlier_after_party_does_not_shorten_period(self):
        """お疲れ様会が会期終了より早い設定でも会期終了日までは表示する。"""
        self.collaboration.settings_json['schedule_milestones']['after_party']['date'] = '2026-12-15'
        self.collaboration.save(update_fields=['settings_json'])
        self.assertIsNotNone(self._get_response(date(2026, 12, 20)).context['vket_banner'])
        self.assertIsNone(self._get_response(date(2026, 12, 21)).context['vket_banner'])

    def test_invalid_settings_are_ignored(self):
        """設定全体・節目・日付が不正なら無視し、会期後まで表示を延ばさない。"""
        self._apply()
        for settings in (
            None, [], 'invalid', {}, {'schedule_milestones': []},
            {'schedule_milestones': {'kickoff': 'invalid', 'after_party': False}},
            {'schedule_milestones': {'kickoff': {'date': None}, 'after_party': {'date': 20261225}}},
            {'schedule_milestones': {'kickoff': {'date': '2026-02-30'}, 'after_party': {'date': 'bad'}}},
            {'schedule_milestones': {'kickoff': {'date': '20261130'}, 'after_party': {'date': '2026-12-25T21:00'}}},
        ):
            with self.subTest(settings=settings):
                self.collaboration.settings_json = settings
                self.collaboration.save(update_fields=['settings_json'])
                banner = self._get_response(date(2026, 11, 24)).context['vket_banner']
                self.assertEqual(banner['milestone']['key'], 'community_event')
                self.assertIsNone(self._get_response(date(2026, 12, 21)).context['vket_banner'])

    def test_invalid_optional_milestone_fields_are_ignored(self):
        """不正な時刻・名前・暫定値は有効な日付の表示を妨げない。"""
        self._apply()
        kickoff = self.collaboration.settings_json['schedule_milestones']['kickoff']
        for invalid_time in (None, 2100, '25:00', '21:99', '2100', '21:00:00', []):
            with self.subTest(time=invalid_time):
                kickoff.update(time=invalid_time, label=[], tentative='true')
                self.collaboration.save(update_fields=['settings_json'])
                banner = self._get_response(date(2026, 11, 24)).context['vket_banner']
                self.assertEqual(banner['message'], '説明会・キックオフ')
                self.assertEqual(banner['time_display'], '')
                self.assertFalse(banner['tentative'])

    def test_latest_eligible_collaboration_is_selected(self):
        """最新の有効コラボを選び、未来の日程があっても下書き・アーカイブは除く。"""
        for phase in (VketCollaboration.Phase.DRAFT, VketCollaboration.Phase.ARCHIVED):
            VketCollaboration.objects.create(
                slug=f'newer-{phase}', name='Hidden collaboration', phase=phase,
                period_start=date(2027, 1, 1), period_end=date(2027, 1, 10),
                registration_deadline=date(2026, 12, 1), lt_deadline=date(2026, 12, 10),
            )
        banner = self._get_response(date(2026, 10, 10)).context['vket_banner']
        self.assertEqual(banner['collaboration'], self.collaboration)

    def test_day_difference_uses_localdate(self):
        """UTCで前日でも日本時間の日付との差を使う。"""
        with timezone.override('Asia/Tokyo'):
            with patch('django.utils.timezone.now', return_value=datetime(
                2026, 10, 9, 16, 0, tzinfo=datetime_timezone.utc,
            )):
                response = self.client.get(reverse('event:my_list'))
        self.assertEqual(response.context['vket_banner']['days_until'], 22)
