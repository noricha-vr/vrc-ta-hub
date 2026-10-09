"""Vketコラボ機能のテスト."""

from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from community.models import CommunityMember
from tests.factories import make_community, make_event, make_user
from vket.models import (
    VketCollaboration,
    VketParticipation,
)

class VketApplyFlowBase(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = make_user(
            user_name='owner_user',
            email='owner@example.com',
            password='testpass123',
        )
        self.other_user = make_user(
            user_name='other_user',
            email='other@example.com',
            password='testpass123',
        )

        self.community = make_community(
            name='個人開発集会',
            owner=self.owner,
            status='approved',
            frequency='毎週',
            weekdays=[],
            organizers='',
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.other_user,
            role=CommunityMember.Role.STAFF,
        )

        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-2026-apply-test',
            name='Vket 2026 Summer 技術学術WEEK',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.ENTRY_OPEN,
        )

        # 日付選択肢の元になるイベントを作成
        make_event(
            self.community,
            event_date=today,
            start_time='22:00',
            duration=60,
            weekday='',
            accepts_lt_application=True,
        )

    def _set_active_community(self):
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()


    def _make_formset_data(self, lt_rows, initial_forms=0):
        """formset用のPOSTデータを辞書で返すヘルパー"""
        data = {
            'lt-TOTAL_FORMS': str(len(lt_rows)),
            'lt-INITIAL_FORMS': str(initial_forms),
            'lt-MIN_NUM_FORMS': '0',
            'lt-MAX_NUM_FORMS': '20',
        }
        for i, row in enumerate(lt_rows):
            data[f'lt-{i}-speaker'] = row.get('speaker', '')
            data[f'lt-{i}-theme'] = row.get('theme', '')
            data[f'lt-{i}-lt_start_time'] = row.get('lt_start_time', '')
            if row.get('DELETE'):
                data[f'lt-{i}-DELETE'] = 'on'
        return data



class VketManageViewsBase(TestCase):
    def setUp(self):
        self.client = Client()
        cache.clear()
        self.superuser = make_user(
            user_name='admin_user',
            email='admin@example.com',
            password='adminpass123',
            is_staff=True,
            is_superuser=True,
        )
        self.normal_user = make_user(
            user_name='normal_user',
            email='normal@example.com',
            password='testpass123',
        )

        self.community1 = make_community(
            name='集会A',
            status='approved',
            frequency='毎週',
            weekdays=[],
            organizers='',
        )
        self.community2 = make_community(
            name='集会B',
            status='approved',
            frequency='毎週',
            weekdays=[],
            organizers='',
        )

        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-2026-manage-test',
            name='Vket 2026 Summer 技術学術WEEK',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.ENTRY_OPEN,
        )

        # 公開済みイベント（published_event）を使う
        self.event1 = make_event(
            self.community1,
            event_date=today,
            start_time='21:00',
            duration=60,
            weekday='Tue',
            accepts_lt_application=True,
        )
        self.event2 = make_event(
            self.community2,
            event_date=today,
            start_time='21:30',
            duration=60,
            weekday='Tue',
            accepts_lt_application=True,
        )

        self.participation1 = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community1,
            published_event=self.event1,
            confirmed_date=today,
            confirmed_start_time='21:00',
            confirmed_duration=60,
        )

        self.participation2 = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community2,
            published_event=self.event2,
            confirmed_date=today,
            confirmed_start_time='21:30',
            confirmed_duration=60,
        )

    def tearDown(self):
        cache.clear()
