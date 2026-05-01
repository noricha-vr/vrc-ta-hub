"""Vketコラボ機能のテスト."""

from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from community.models import Community, CommunityMember
from event.models import Event, EventDetail
from ta_hub.index_cache import get_index_view_cache_key
from vket.models import (
    VketCollaboration,
    VketNotice,
    VketNoticeReceipt,
    VketParticipation,
    VketPresentation,
)


User = get_user_model()


class VketPublicPagesTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-2026-summer',
            name='Vket 2026 Summer 技術学術WEEK',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.ENTRY_OPEN,
            hashtags=['#Vketステージ', '#Vket技術学術WEEK'],
        )
        self.draft_collaboration = VketCollaboration.objects.create(
            slug='vket-2026-draft',
            name='Vket Draft',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.DRAFT,
        )

    def test_list_page(self):
        client = Client()
        response = client.get(reverse('vket:list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.collaboration.name)
        # 下書きは表示されない
        self.assertNotContains(response, self.draft_collaboration.name)

    def test_detail_page(self):
        client = Client()
        response = client.get(reverse('vket:detail', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.collaboration.name)

    def test_detail_page_is_404_for_draft(self):
        client = Client()
        response = client.get(reverse('vket:detail', kwargs={'pk': self.draft_collaboration.pk}))
        self.assertEqual(response.status_code, 404)

    def test_collaboration_validation(self):
        today = timezone.localdate()
        collaboration = VketCollaboration(
            slug='invalid-collab',
            name='Invalid collaboration',
            period_start=today + timedelta(days=1),
            period_end=today,
            registration_deadline=today,
            lt_deadline=today - timedelta(days=1),
            phase=VketCollaboration.Phase.DRAFT,
        )
        with self.assertRaises(ValidationError):
            collaboration.full_clean()


class VketApplyFlowTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(
            user_name='owner_user',
            email='owner@example.com',
            password='testpass123',
        )
        self.other_user = User.objects.create_user(
            user_name='other_user',
            email='other@example.com',
            password='testpass123',
        )

        self.community = Community.objects.create(
            name='個人開発集会',
            status='approved',
            frequency='毎週',
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER,
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
        Event.objects.create(
            community=self.community,
            date=today,
            start_time='22:00',
            duration=60,
        )

    def _set_active_community(self):
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

    def test_apply_get_allows_staff(self):
        """スタッフもapplyページにアクセスできる"""
        self.client.login(username='other_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)

    def test_apply_get_shows_organizer_note_guidance_above_textarea(self):
        """備考欄の案内文がテキストエリア外に表示される"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'スタッフが少なく、当日ステージへの案内などサポートが欲しい場合は「サポート希望」と記載してください。',
        )
        self.assertContains(
            response,
            'その他、運営に連絡事項があれば記載をお願いします。',
        )
        self.assertContains(response, 'background: #eef6ff; color: #2f5f8f;')
        self.assertContains(response, 'text-decoration-underline')

    def test_apply_post_creates_participation_and_presentation(self):
        """主催者が申請するとVketParticipationとVketPresentationが作成される"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        target_date = self.collaboration.period_start
        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data={
                'requested_date': target_date.isoformat(),
                'requested_start_time': '21:00',
                'requested_duration': '60',
                'organizer_note': '備考テスト',
                # formset management form
                'lt-TOTAL_FORMS': '1',
                'lt-INITIAL_FORMS': '0',
                'lt-MIN_NUM_FORMS': '0',
                'lt-MAX_NUM_FORMS': '20',
                # LT data
                'lt-0-speaker': 'テスト登壇者',
                'lt-0-theme': 'テストテーマ',
                'lt-0-lt_start_time': '',
            },
            follow=False,
        )

        self.assertEqual(response.status_code, 302)

        participation = VketParticipation.objects.get(
            collaboration=self.collaboration, community=self.community
        )
        # 希望日程が保存されている
        self.assertEqual(participation.requested_date, target_date)
        self.assertEqual(participation.requested_start_time.strftime('%H:%M'), '21:00')
        self.assertEqual(participation.requested_duration, 60)
        self.assertEqual(participation.organizer_note, '備考テスト')
        # applied_byがセットされている
        self.assertEqual(participation.applied_by, self.owner)
        self.assertIsNotNone(participation.applied_at)
        self.assertEqual(participation.progress, VketParticipation.Progress.APPLIED)

        # 確定前なのでEventは作成されない
        self.assertIsNone(participation.published_event_id)

        # VketPresentationが作成されている
        pres = VketPresentation.objects.get(participation=participation, order=0)
        self.assertEqual(pres.speaker, 'テスト登壇者')
        self.assertEqual(pres.theme, 'テストテーマ')

    def test_apply_is_forbidden_after_registration_deadline_for_new_participation(self):
        """参加申請締切後は新規参加登録が403になる"""
        self.collaboration.registration_deadline = timezone.localdate() - timedelta(days=1)
        self.collaboration.save()

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 403)

    def test_detail_can_apply_is_false_when_registration_closed_and_no_participation(self):
        """締切後・未参加の場合 can_apply=False"""
        self.collaboration.registration_deadline = timezone.localdate() - timedelta(days=1)
        self.collaboration.save()

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:detail', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_apply'])

    def test_detail_can_apply_is_true_when_lt_open_and_participation_has_published_event(self):
        """LT締切内かつpublished_eventがある場合 can_apply=True"""
        today = timezone.localdate()
        self.collaboration.registration_deadline = today - timedelta(days=1)
        self.collaboration.lt_deadline = today + timedelta(days=1)
        self.collaboration.phase = VketCollaboration.Phase.SCHEDULING
        self.collaboration.save()

        weekday_code = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][today.weekday()]
        event = Event.objects.create(
            community=self.community,
            date=today,
            start_time='21:00',
            duration=60,
            weekday=weekday_code,
        )
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            published_event=event,
        )

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:detail', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_apply'])

    def test_status_page_shows_register_complete_guidance(self):
        """参加状況画面で登録後の次アクションが明示される"""
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            applied_by=self.owner,
            applied_at=timezone.now(),
            progress=VketParticipation.Progress.APPLIED,
        )

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        response = self.client.get(reverse('vket:status', kwargs={'pk': self.collaboration.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'この画面に戻って下の「登録完了」を押してください。',
        )
        self.assertContains(
            response,
            'Hub 側で「登録完了」の操作が必要です。',
        )

    def test_lt_start_time_saved_to_presentation(self):
        """LT開始時刻が VketPresentation.requested_start_time に保存される"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        target_date = self.collaboration.period_start
        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data={
                'requested_date': target_date.isoformat(),
                'requested_start_time': '21:00',
                'requested_duration': '60',
                'organizer_note': '備考テスト',
                'lt-TOTAL_FORMS': '1',
                'lt-INITIAL_FORMS': '0',
                'lt-MIN_NUM_FORMS': '0',
                'lt-MAX_NUM_FORMS': '20',
                'lt-0-speaker': 'テスト登壇者',
                'lt-0-theme': 'テストテーマ',
                'lt-0-lt_start_time': '21:30',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        participation = VketParticipation.objects.get(
            collaboration=self.collaboration, community=self.community
        )
        pres = VketPresentation.objects.get(participation=participation, order=0)
        self.assertEqual(pres.requested_start_time.strftime('%H:%M'), '21:30')

    def test_new_apply_shows_organizer_note_template(self):
        """新規申請GETで organizer_note の初期値テンプレートが表示される"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        response = self.client.get(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertIn('当日サポートが欲しい', form.initial.get('organizer_note', ''))

    def test_existing_participation_preserves_organizer_note(self):
        """既存参加者のGETで organizer_note が初期テンプレートで上書きされない"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        # 先に参加を作成（副作用でDBレコードを作成）
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            organizer_note='カスタム備考',
            progress=VketParticipation.Progress.APPLIED,
        )

        response = self.client.get(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form.initial.get('organizer_note'), 'カスタム備考')

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

    def test_apply_creates_multiple_presentations(self):
        """複数LTを送信するとDBに複数件作成される"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        target_date = self.collaboration.period_start
        lt_rows = [
            {'speaker': '登壇者A', 'theme': 'テーマA'},
            {'speaker': '登壇者B', 'theme': 'テーマB'},
            {'speaker': '登壇者C', 'theme': 'テーマC'},
        ]
        post_data = {
            'requested_date': target_date.isoformat(),
            'requested_start_time': '21:00',
            'requested_duration': '60',
            'organizer_note': '',
        }
        post_data.update(self._make_formset_data(lt_rows))

        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data=post_data,
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        participation = VketParticipation.objects.get(
            collaboration=self.collaboration, community=self.community
        )
        presentations = list(
            VketPresentation.objects.filter(participation=participation).order_by('order')
        )
        self.assertEqual(len(presentations), 3)
        self.assertEqual(presentations[0].speaker, '登壇者A')
        self.assertEqual(presentations[0].order, 0)
        self.assertEqual(presentations[1].speaker, '登壇者B')
        self.assertEqual(presentations[1].order, 1)
        self.assertEqual(presentations[2].speaker, '登壇者C')
        self.assertEqual(presentations[2].order, 2)

    def test_apply_deletes_presentation(self):
        """DELETEフラグ付きで送信すると該当レコードが削除される"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        # 先に参加と2件のプレゼンを作成
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time='21:00',
            requested_duration=60,
            progress=VketParticipation.Progress.APPLIED,
            applied_by=self.owner,
        )
        VketPresentation.objects.create(
            participation=participation, order=0, speaker='残す登壇者', theme='残すテーマ'
        )
        VketPresentation.objects.create(
            participation=participation, order=1, speaker='消す登壇者', theme='消すテーマ'
        )

        target_date = self.collaboration.period_start
        lt_rows = [
            {'speaker': '残す登壇者', 'theme': '残すテーマ'},
            {'speaker': '消す登壇者', 'theme': '消すテーマ', 'DELETE': True},
        ]
        post_data = {
            'requested_date': target_date.isoformat(),
            'requested_start_time': '21:00',
            'requested_duration': '60',
            'organizer_note': '',
        }
        post_data.update(self._make_formset_data(lt_rows, initial_forms=2))

        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data=post_data,
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        presentations = list(
            VketPresentation.objects.filter(participation=participation).order_by('order')
        )
        self.assertEqual(len(presentations), 1)
        self.assertEqual(presentations[0].speaker, '残す登壇者')

    def test_confirmed_participation_post_keeps_schedule_and_lt_start_time(self):
        """日程確定後の主催者POSTでは日程と既存LT開始時刻が変わらない"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time=time(21, 0),
            requested_duration=60,
            confirmed_date=self.collaboration.period_start,
            confirmed_start_time=time(21, 0),
            confirmed_duration=60,
            schedule_confirmed_at=timezone.now(),
            progress=VketParticipation.Progress.REHEARSAL,
            applied_by=self.owner,
        )
        presentation = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='確定前登壇者',
            theme='確定前テーマ',
            requested_start_time=time(21, 30),
            status=VketPresentation.Status.CONFIRMED,
        )

        post_data = {
            'requested_date': (self.collaboration.period_start + timedelta(days=1)).isoformat(),
            'requested_start_time': '23:00',
            'requested_duration': '90',
            'organizer_note': '確定後も備考は更新',
        }
        post_data.update(
            self._make_formset_data(
                [
                    {
                        'speaker': '更新後登壇者',
                        'theme': '更新後テーマ',
                        'lt_start_time': '23:30',
                    }
                ],
                initial_forms=1,
            )
        )

        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data=post_data,
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        participation.refresh_from_db()
        presentation.refresh_from_db()
        self.assertEqual(participation.requested_date, self.collaboration.period_start)
        self.assertEqual(participation.requested_start_time, time(21, 0))
        self.assertEqual(participation.requested_duration, 60)
        self.assertEqual(participation.organizer_note, '確定後も備考は更新')
        self.assertEqual(presentation.speaker, '更新後登壇者')
        self.assertEqual(presentation.theme, '更新後テーマ')
        self.assertEqual(presentation.requested_start_time, time(21, 30))

    def test_confirmed_participation_post_does_not_delete_existing_lt(self):
        """日程確定後はformset DELETEでも既存LTを削除しない"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time=time(21, 0),
            requested_duration=60,
            confirmed_date=self.collaboration.period_start,
            confirmed_start_time=time(21, 0),
            confirmed_duration=60,
            schedule_confirmed_at=timezone.now(),
            progress=VketParticipation.Progress.REHEARSAL,
        )
        presentation = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='削除されない登壇者',
            theme='削除されないテーマ',
            requested_start_time=time(21, 30),
        )

        post_data = {
            'requested_date': self.collaboration.period_start.isoformat(),
            'requested_start_time': '21:00',
            'requested_duration': '60',
            'organizer_note': '',
        }
        post_data.update(
            self._make_formset_data(
                [
                    {
                        'speaker': '削除されない登壇者',
                        'theme': '削除されないテーマ',
                        'DELETE': True,
                    }
                ],
                initial_forms=1,
            )
        )

        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data=post_data,
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(VketPresentation.objects.filter(pk=presentation.pk).exists())

    def test_confirmed_participation_apply_get_shows_locked_message(self):
        """日程確定後の参加フォームに編集不可の文言が出る"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time=time(21, 0),
            requested_duration=60,
            confirmed_date=self.collaboration.period_start,
            confirmed_start_time=time(21, 0),
            confirmed_duration=60,
            schedule_confirmed_at=timezone.now(),
        )

        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '参加日程（Step 1）は運営が確定済みのため編集できません。')
        self.assertContains(response, 'LT開始時刻は日程確定済みのため編集できません。')

    def test_apply_get_prefills_multiple_presentations(self):
        """既存の複数LTがGETでformsetにプリフィルされる"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time='21:00',
            requested_duration=60,
            progress=VketParticipation.Progress.APPLIED,
            applied_by=self.owner,
        )
        VketPresentation.objects.create(
            participation=participation, order=0, speaker='登壇者1', theme='テーマ1'
        )
        VketPresentation.objects.create(
            participation=participation, order=1, speaker='登壇者2', theme='テーマ2'
        )
        VketPresentation.objects.create(
            participation=participation, order=2, speaker='登壇者3', theme='テーマ3'
        )

        response = self.client.get(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)

        formset = response.context['formset']
        # initial + extra(1) = 4 forms
        self.assertEqual(len(formset.forms), 4)
        self.assertEqual(formset.forms[0].initial['speaker'], '登壇者1')
        self.assertEqual(formset.forms[1].initial['speaker'], '登壇者2')
        self.assertEqual(formset.forms[2].initial['speaker'], '登壇者3')

    def test_apply_skips_empty_presentation_rows(self):
        """空行はスキップされる（DBに保存されない）"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        target_date = self.collaboration.period_start
        lt_rows = [
            {'speaker': '登壇者A', 'theme': 'テーマA'},
            {'speaker': '', 'theme': ''},
            {'speaker': '登壇者C', 'theme': 'テーマC'},
        ]
        post_data = {
            'requested_date': target_date.isoformat(),
            'requested_start_time': '21:00',
            'requested_duration': '60',
            'organizer_note': '',
        }
        post_data.update(self._make_formset_data(lt_rows))

        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data=post_data,
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        participation = VketParticipation.objects.get(
            collaboration=self.collaboration, community=self.community
        )
        presentations = list(
            VketPresentation.objects.filter(participation=participation).order_by('order')
        )
        self.assertEqual(len(presentations), 2)
        self.assertEqual(presentations[0].speaker, '登壇者A')
        self.assertEqual(presentations[0].order, 0)
        self.assertEqual(presentations[1].speaker, '登壇者C')
        self.assertEqual(presentations[1].order, 1)

    def test_presentation_delete_by_organizer(self):
        """主催者がLTを削除できる"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            progress=VketParticipation.Progress.APPLIED,
        )
        pres = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='削除テスト',
            theme='テーマ',
            status=VketPresentation.Status.DRAFT,
        )
        response = self.client.post(
            reverse(
                'vket:presentation_delete',
                kwargs={'pk': self.collaboration.pk, 'presentation_id': pres.pk},
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(VketPresentation.objects.filter(pk=pres.pk).exists())

    def test_confirmed_presentation_delete_forbidden_for_organizer(self):
        """確定済みLTは主催者側の個別削除を拒否する"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            confirmed_date=self.collaboration.period_start,
            confirmed_start_time=time(21, 0),
            confirmed_duration=60,
            schedule_confirmed_at=timezone.now(),
            progress=VketParticipation.Progress.REHEARSAL,
        )
        pres = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='確定済みLT',
            theme='テーマ',
            status=VketPresentation.Status.CONFIRMED,
        )

        response = self.client.post(
            reverse(
                'vket:presentation_delete',
                kwargs={'pk': self.collaboration.pk, 'presentation_id': pres.pk},
            ),
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(VketPresentation.objects.filter(pk=pres.pk).exists())

    def test_published_presentation_delete_forbidden_for_organizer(self):
        """公開済みLTは主催者側の個別削除を拒否する"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        event = Event.objects.filter(community=self.community).first()
        detail = EventDetail.objects.create(
            event=event,
            detail_type='LT',
            start_time='21:30',
            duration=30,
            status='approved',
        )
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            progress=VketParticipation.Progress.APPLIED,
        )
        pres = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='公開済みLT',
            theme='テーマ',
            published_event_detail=detail,
        )

        response = self.client.post(
            reverse(
                'vket:presentation_delete',
                kwargs={'pk': self.collaboration.pk, 'presentation_id': pres.pk},
            ),
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(VketPresentation.objects.filter(pk=pres.pk).exists())
        self.assertTrue(EventDetail.objects.filter(pk=detail.pk).exists())

    def test_presentation_delete_forbidden_for_non_member(self):
        """コミュニティに所属しないユーザーはLTを削除できない"""
        User.objects.create_user(
            user_name='non_member_user',
            email='nonmember@example.com',
            password='testpass123',
        )
        self.client.login(username='non_member_user', password='testpass123')
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            progress=VketParticipation.Progress.APPLIED,
        )
        pres = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='テスト',
            status=VketPresentation.Status.DRAFT,
        )
        response = self.client.post(
            reverse(
                'vket:presentation_delete',
                kwargs={'pk': self.collaboration.pk, 'presentation_id': pres.pk},
            ),
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(VketPresentation.objects.filter(pk=pres.pk).exists())


class VketManageViewsTests(TestCase):
    def setUp(self):
        self.client = Client()
        cache.clear()
        self.superuser = User.objects.create_superuser(
            user_name='admin_user',
            email='admin@example.com',
            password='adminpass123',
        )
        self.normal_user = User.objects.create_user(
            user_name='normal_user',
            email='normal@example.com',
            password='testpass123',
        )

        self.community1 = Community.objects.create(
            name='集会A',
            status='approved',
            frequency='毎週',
        )
        self.community2 = Community.objects.create(
            name='集会B',
            status='approved',
            frequency='毎週',
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
        self.event1 = Event.objects.create(
            community=self.community1,
            date=today,
            start_time='21:00',
            duration=60,
            weekday='Tue',
        )
        self.event2 = Event.objects.create(
            community=self.community2,
            date=today,
            start_time='21:30',
            duration=60,
            weekday='Tue',
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

    def test_manage_page_requires_staff(self):
        """管理画面はstaff権限が必要"""
        self.client.login(username='normal_user', password='testpass123')
        response = self.client.get(reverse('vket:manage', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 403)

    def test_manage_page_shows_collaboration(self):
        """管理画面にコラボ名と集会名が表示される"""
        self.client.login(username='admin_user', password='adminpass123')
        response = self.client.get(reverse('vket:manage', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.collaboration.name)
        self.assertContains(response, self.community1.name)
        self.assertContains(response, 'name="lifecycle"')
        self.assertContains(response, '不参加')
        self.assertContains(response, '辞退')

    def test_manage_schedule_page_shows_overlap_warning(self):
        """日程重複がある場合にoverlap_warningsがセットされる"""
        self.client.login(username='admin_user', password='adminpass123')
        response = self.client.get(
            reverse('vket:manage_schedule', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['overlap_warnings'])

    def test_manage_schedule_shows_confirmed_without_published_event(self):
        """confirmed_dateがあればpublished_eventなしでも日程表に表示される"""
        community3 = Community.objects.create(name='集会C', status='approved', frequency='毎週')
        today = timezone.localdate()
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=community3,
            confirmed_date=today,
            confirmed_start_time='22:00',
            confirmed_duration=60,
        )

        self.client.login(username='admin_user', password='adminpass123')
        response = self.client.get(
            reverse('vket:manage_schedule', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)

        rows = response.context['rows']
        communities = [r['participation'].community.name for r in rows]
        self.assertIn('集会C', communities)

    def test_manage_schedule_page_marks_lt_slot(self):
        """LT詳細があるスロットにlt_timesが設定される"""
        EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            start_time='21:30',
            duration=30,
            status='approved',
        )

        self.client.login(username='admin_user', password='adminpass123')
        response = self.client.get(
            reverse('vket:manage_schedule', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)

        slots = response.context['slots']
        expected_idx = next(i for i, s in enumerate(slots) if s.start == time(21, 30))

        rows = response.context['rows']
        row = next(r for r in rows if r['participation'].pk == self.participation1.pk)
        lt_indices = [i for i, cell in enumerate(row['cells']) if cell.get('lt_times')]
        self.assertEqual(lt_indices, [expected_idx])
        self.assertEqual(row['cells'][expected_idx]['lt_times'], [time(21, 30)])

    def test_manage_participation_update_sets_confirmed_fields(self):
        """ManageParticipationUpdateViewが確定日程・progressを正しくセットする"""
        self.client.login(username='admin_user', password='adminpass123')
        # 未確定の参加を作成してテスト
        new_participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=Community.objects.create(name='集会C', status='approved', frequency='毎週'),
            requested_date=self.collaboration.period_start,
            requested_start_time='22:00',
            requested_duration=60,
        )
        new_date = self.collaboration.period_start + timedelta(days=1)
        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': new_participation.pk,
                },
            ),
            data={
                'confirmed_date': new_date.isoformat(),
                'confirmed_start_time': '22:00',
                'confirmed_duration': '60',
                'admin_note': '確定しました',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        new_participation.refresh_from_db()
        self.assertEqual(new_participation.confirmed_date, new_date)
        self.assertEqual(new_participation.confirmed_start_time.strftime('%H:%M'), '22:00')
        self.assertEqual(new_participation.confirmed_duration, 60)
        self.assertEqual(new_participation.admin_note, '確定しました')
        self.assertTrue(new_participation.schedule_adjusted_by_admin)
        self.assertEqual(new_participation.progress, VketParticipation.Progress.REHEARSAL)
        self.assertIsNotNone(new_participation.schedule_confirmed_at)

    def test_manage_participation_update_sets_lifecycle_without_schedule(self):
        """管理画面から参加状態だけを不参加に変更できる"""
        self.client.login(username='admin_user', password='adminpass123')
        new_participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=Community.objects.create(name='集会C', status='approved', frequency='毎週'),
            requested_date=self.collaboration.period_start,
            requested_start_time='22:00',
            requested_duration=60,
        )
        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': new_participation.pk,
                },
            ),
            data={
                'lifecycle': VketParticipation.Lifecycle.DECLINED,
                'admin_note': '今回は不参加',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        new_participation.refresh_from_db()
        self.assertEqual(new_participation.lifecycle, VketParticipation.Lifecycle.DECLINED)
        self.assertEqual(new_participation.admin_note, '今回は不参加')
        self.assertFalse(new_participation.schedule_adjusted_by_admin)
        self.assertIsNone(new_participation.schedule_confirmed_at)

    def test_manage_participation_update_declined_clears_published_event(self):
        """公開済み参加を不参加にすると公開イベント連携を解除する"""
        self.client.login(username='admin_user', password='adminpass123')
        detail = EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            speaker='公開済み登壇者',
            theme='公開済みテーマ',
            start_time='21:30',
            duration=30,
            status='approved',
        )
        presentation = VketPresentation.objects.create(
            participation=self.participation1,
            order=0,
            speaker='公開済み登壇者',
            theme='公開済みテーマ',
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=detail,
        )

        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': self.participation1.pk,
                },
            ),
            data={
                'lifecycle': VketParticipation.Lifecycle.DECLINED,
                'admin_note': '公開後に不参加',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        self.participation1.refresh_from_db()
        presentation.refresh_from_db()
        self.assertEqual(self.participation1.lifecycle, VketParticipation.Lifecycle.DECLINED)
        self.assertIsNone(self.participation1.published_event_id)
        self.assertIsNone(presentation.published_event_detail_id)
        self.assertFalse(Event.objects.filter(pk=self.event1.pk).exists())
        self.assertFalse(EventDetail.objects.filter(pk=detail.pk).exists())

    def test_manage_participation_update_requires_schedule_for_active(self):
        """参加中で保存する場合は確定日程が必須"""
        self.client.login(username='admin_user', password='adminpass123')
        new_participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=Community.objects.create(name='集会C', status='approved', frequency='毎週'),
        )
        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': new_participation.pk,
                },
            ),
            data={
                'lifecycle': VketParticipation.Lifecycle.ACTIVE,
                'admin_note': '日程なし',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        new_participation.refresh_from_db()
        self.assertIsNone(new_participation.confirmed_date)
        self.assertFalse(new_participation.schedule_adjusted_by_admin)

    def test_manage_participation_update_updates_event_detail_start_time(self):
        """日程確定時にEventDetailのLT開始時刻が更新される"""
        self.client.login(username='admin_user', password='adminpass123')
        # EventDetailを作成
        detail = EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            start_time='21:30',
            duration=30,
            status='approved',
        )
        cache_key = get_index_view_cache_key()
        cache.set(cache_key, {'upcoming_event_details': ['stale']}, 60)
        new_date = self.collaboration.period_start
        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': self.participation1.pk,
                },
            ),
            data={
                'confirmed_date': new_date.isoformat(),
                'confirmed_start_time': '21:00',
                'confirmed_duration': '60',
                'admin_note': '',
                f'detail_{detail.pk}_start_time': '22:15',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        detail.refresh_from_db()
        self.assertEqual(detail.start_time.strftime('%H:%M'), '22:15')
        self.assertIsNone(cache.get(cache_key))

    def test_manage_participation_update_rejects_foreign_event_detail(self):
        """別のイベントのEventDetailは更新できない"""
        self.client.login(username='admin_user', password='adminpass123')
        # event2に属するdetailをevent1の参加で更新しようとする
        foreign_detail = EventDetail.objects.create(
            event=self.event2,
            detail_type='LT',
            start_time='21:30',
            duration=30,
            status='approved',
        )
        new_date = self.collaboration.period_start
        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': self.participation1.pk,
                },
            ),
            data={
                'confirmed_date': new_date.isoformat(),
                'confirmed_start_time': '21:00',
                'confirmed_duration': '60',
                'admin_note': '',
                f'detail_{foreign_detail.pk}_start_time': '23:00',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        # 別イベントのdetailは更新されない
        foreign_detail.refresh_from_db()
        self.assertEqual(foreign_detail.start_time.strftime('%H:%M'), '21:30')

    def test_manage_update_confirms_draft_presentations(self):
        """確定ボタン押下でDRAFTのLTがCONFIRMEDに一括更新される"""
        self.client.login(username='admin_user', password='adminpass123')
        new_community = Community.objects.create(
            name='集会D', status='approved', frequency='毎週',
        )
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=new_community,
            requested_date=self.collaboration.period_start,
            requested_start_time='22:00',
            requested_duration=60,
        )
        draft_pres = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='DRAFT登壇者',
            theme='DRAFTテーマ',
            status=VketPresentation.Status.DRAFT,
        )
        confirmed_pres = VketPresentation.objects.create(
            participation=participation,
            order=1,
            speaker='確定済み登壇者',
            theme='確定済みテーマ',
            status=VketPresentation.Status.CONFIRMED,
        )

        new_date = self.collaboration.period_start + timedelta(days=1)
        self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': participation.pk,
                },
            ),
            data={
                'confirmed_date': new_date.isoformat(),
                'confirmed_start_time': '22:00',
                'confirmed_duration': '60',
            },
        )

        draft_pres.refresh_from_db()
        confirmed_pres.refresh_from_db()
        self.assertEqual(draft_pres.status, VketPresentation.Status.CONFIRMED)
        self.assertEqual(confirmed_pres.status, VketPresentation.Status.CONFIRMED)

    def test_manage_update_creates_event_detail_for_new_presentation(self):
        """確定ボタンでpublished_event_detailがないCONFIRMED LTにEventDetailが作成される"""
        self.client.login(username='admin_user', password='adminpass123')
        # published_event_detail がない DRAFT のLTを追加
        pres = VketPresentation.objects.create(
            participation=self.participation1,
            order=1,
            speaker='新規登壇者',
            theme='新規テーマ',
            requested_start_time='21:45',
            status=VketPresentation.Status.DRAFT,
        )
        cache_key = get_index_view_cache_key()
        cache.set(cache_key, {'upcoming_event_details': ['stale']}, 60)
        new_date = self.collaboration.period_start + timedelta(days=1)
        self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': self.participation1.pk,
                },
            ),
            data={
                'confirmed_date': new_date.isoformat(),
                'confirmed_start_time': '21:00',
                'confirmed_duration': '60',
            },
        )
        pres.refresh_from_db()
        self.assertEqual(pres.status, VketPresentation.Status.CONFIRMED)
        self.assertIsNotNone(pres.published_event_detail)
        self.assertEqual(pres.published_event_detail.speaker, '新規登壇者')
        self.assertEqual(pres.published_event_detail.event_id, self.event1.id)
        self.assertIsNone(cache.get(cache_key))

    def test_manage_page_shows_draft_badge(self):
        """管理画面でDRAFTのLTに「申請中」バッジが表示される"""
        self.client.login(username='admin_user', password='adminpass123')
        VketPresentation.objects.create(
            participation=self.participation1,
            order=0,
            speaker='DRAFT登壇者',
            theme='DRAFTテーマ',
            status=VketPresentation.Status.DRAFT,
        )
        response = self.client.get(
            reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'DRAFT登壇者')
        self.assertContains(response, '申請中')
        self.assertContains(response, 'badge bg-warning')

    def test_manage_page_shows_lt_time_badge(self):
        """管理画面でLT時間バッジが表示される"""
        self.client.login(username='admin_user', password='adminpass123')
        # プレゼンテーションとEventDetailを作成
        detail = EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            start_time='21:30',
            duration=30,
            status='approved',
        )
        VketPresentation.objects.create(
            participation=self.participation1,
            order=0,
            speaker='テスト登壇者',
            theme='テストテーマ',
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=detail,
        )

        response = self.client.get(
            reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '21:30')
        self.assertContains(response, 'badge bg-info')

    def test_manage_presentation_delete_removes_presentation_and_event_detail(self):
        """管理者がLTを削除するとVketPresentationとEventDetailが両方削除される"""
        self.client.login(username='admin_user', password='adminpass123')
        detail = EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            start_time='21:30',
            duration=30,
            status='approved',
        )
        pres = VketPresentation.objects.create(
            participation=self.participation1,
            order=0,
            speaker='削除テスト登壇者',
            theme='削除テーマ',
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=detail,
        )
        response = self.client.post(
            reverse(
                'vket:manage_presentation_delete',
                kwargs={'pk': self.collaboration.pk, 'presentation_id': pres.pk},
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(VketPresentation.objects.filter(pk=pres.pk).exists())
        self.assertFalse(EventDetail.objects.filter(pk=detail.pk).exists())

    def test_manage_presentation_delete_requires_staff(self):
        """一般ユーザー（非staff）はLTを削除できない"""
        self.client.login(username='normal_user', password='testpass123')
        pres = VketPresentation.objects.create(
            participation=self.participation1,
            order=0,
            speaker='テスト',
            status=VketPresentation.Status.DRAFT,
        )
        response = self.client.post(
            reverse(
                'vket:manage_presentation_delete',
                kwargs={'pk': self.collaboration.pk, 'presentation_id': pres.pk},
            ),
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(VketPresentation.objects.filter(pk=pres.pk).exists())


class VketStaffAccessTests(TestCase):
    """is_staff=True, is_superuser=False のユーザーで管理機能にアクセスできることを検証."""

    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_user(
            user_name='staff_user',
            email='staff@example.com',
            password='staffpass123',
            is_staff=True,
        )

        self.community = Community.objects.create(
            name='Staff集会',
            status='approved',
            frequency='毎週',
        )

        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-staff-test',
            name='Vket Staff Test',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.ENTRY_OPEN,
        )
        self.draft_collaboration = VketCollaboration.objects.create(
            slug='vket-staff-draft',
            name='Vket Staff Draft',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.DRAFT,
        )

        self.event = Event.objects.create(
            community=self.community,
            date=today,
            start_time='21:00',
            duration=60,
        )
        self.participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            published_event=self.event,
            confirmed_date=today,
            confirmed_start_time='21:00',
            confirmed_duration=60,
        )

        self.notice = VketNotice.objects.create(
            collaboration=self.collaboration,
            title='Staff通知テスト',
            body='テスト本文',
            created_by=self.staff_user,
        )

    def test_staff_can_access_manage_page(self):
        """staffユーザーが管理画面にアクセスできる"""
        self.client.login(username='staff_user', password='staffpass123')
        response = self.client.get(reverse('vket:manage', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)

    def test_staff_can_see_draft_collaboration(self):
        """staffユーザーにDRAFTコラボが表示される"""
        self.client.login(username='staff_user', password='staffpass123')
        response = self.client.get(reverse('vket:list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.draft_collaboration.name)

    def test_staff_can_access_manage_notice_list(self):
        """staffユーザーが管理用お知らせ一覧にアクセスできる"""
        self.client.login(username='staff_user', password='staffpass123')
        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)

    def test_staff_sees_admin_link_on_participation_status(self):
        """staffユーザーの参加状況ページに管理リンクが表示される"""
        CommunityMember.objects.create(
            community=self.community,
            user=self.staff_user,
            role=CommunityMember.Role.OWNER,
        )
        self.client.login(username='staff_user', password='staffpass123')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        response = self.client.get(reverse('vket:status', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '管理')


class BuildDiscordMentionsTests(TestCase):
    """_build_discord_mentionsのユニットテスト."""

    def setUp(self):
        from allauth.socialaccount.models import SocialAccount

        self.user1 = User.objects.create_user(
            user_name='user1', email='u1@example.com', password='pass',
        )
        self.user2 = User.objects.create_user(
            user_name='user2', email='u2@example.com', password='pass',
        )
        self.user3 = User.objects.create_user(
            user_name='user3', email='u3@example.com', password='pass',
        )
        SocialAccount.objects.create(user=self.user1, provider='discord', uid='111')
        SocialAccount.objects.create(user=self.user2, provider='discord', uid='222')
        SocialAccount.objects.create(user=self.user3, provider='discord', uid='333')

        community1 = Community.objects.create(name='集会1', status='approved', frequency='毎週')
        community2 = Community.objects.create(name='集会2', status='approved', frequency='毎週')
        community3 = Community.objects.create(name='集会3', status='approved', frequency='毎週')
        today = timezone.localdate()
        self.collab = VketCollaboration.objects.create(
            slug='mention-test', name='テスト', period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=3),
            lt_deadline=today + timedelta(days=5),
            phase=VketCollaboration.Phase.ENTRY_OPEN,
        )

        self.p1 = VketParticipation.objects.create(
            collaboration=self.collab, community=community1,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
            applied_by=self.user1, progress=VketParticipation.Progress.APPLIED,
        )
        self.p2 = VketParticipation.objects.create(
            collaboration=self.collab, community=community2,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
            applied_by=self.user2, progress=VketParticipation.Progress.STAGE_REGISTERED,
        )
        self.p3 = VketParticipation.objects.create(
            collaboration=self.collab, community=community3,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
            applied_by=self.user3, progress=VketParticipation.Progress.REHEARSAL,
        )

    def _call(self):
        from vket.views import ManageView
        parts = VketParticipation.objects.filter(collaboration=self.collab)
        return ManageView._build_discord_mentions(parts)

    def test_stage_not_registered_contains_applied(self):
        """APPLIED状態の参加者がstage_not_registeredメンションに含まれる."""
        result = self._call()
        self.assertIn('<@111>', result['stage_not_registered'])
        self.assertNotIn('<@222>', result['stage_not_registered'])

    def test_lt_not_registered_contains_stage_registered(self):
        """STAGE_REGISTERED状態の参加者がlt_not_registeredメンションに含まれる."""
        result = self._call()
        self.assertIn('<@222>', result['lt_not_registered'])
        self.assertNotIn('<@111>', result['lt_not_registered'])

    def test_rehearsal_not_in_individual_groups(self):
        """REHEARSAL状態の参加者は個別グループに含まれずallのみに含まれる."""
        result = self._call()
        self.assertIn('<@333>', result['all'])
        self.assertNotIn('<@333>', result['stage_not_registered'])
        self.assertNotIn('<@333>', result['lt_not_registered'])
        self.assertNotIn('<@333>', result['not_applied'])

    def test_all_contains_everyone(self):
        """全参加者メンションに全員が含まれる."""
        result = self._call()
        self.assertIn('<@111>', result['all'])
        self.assertIn('<@222>', result['all'])
        self.assertIn('<@333>', result['all'])


class VketNoticeTests(TestCase):
    """お知らせ・ACK機能のテスト"""

    def setUp(self):
        self.client = Client()
        self.superuser = User.objects.create_superuser(
            user_name='admin_user2',
            email='admin2@example.com',
            password='adminpass123',
        )
        self.owner = User.objects.create_user(
            user_name='owner_user2',
            email='owner2@example.com',
            password='testpass123',
        )
        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週',
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER,
        )

        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-2026-notice-test',
            name='お知らせテスト',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.SCHEDULING,
        )
        self.participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )
        self.notice = VketNotice.objects.create(
            collaboration=self.collaboration,
            title='テストお知らせ',
            body='テスト本文',
            requires_ack=True,
            target_scope=VketNotice.TargetScope.ALL_PARTICIPANTS,
            created_by=self.superuser,
        )

    def test_ack_notice_view_get_does_not_mark_acknowledged(self):
        """AckNoticeView GET はプレビュー表示のみ（DB書き換えなし）"""
        receipt = VketNoticeReceipt.objects.create(
            notice=self.notice,
            participation=self.participation,
        )
        self.assertIsNone(receipt.acknowledged_at)

        response = self.client.get(
            reverse('vket:ack_notice', kwargs={'ack_token': str(receipt.ack_token)})
        )
        self.assertEqual(response.status_code, 200)

        receipt.refresh_from_db()
        self.assertIsNone(receipt.acknowledged_at)  # GETでは変更されない
        self.assertFalse(response.context['already_acked'])

    def test_ack_notice_view_post_marks_acknowledged(self):
        """AckNoticeView POST でreceiptのacknowledged_atがセットされる"""
        receipt = VketNoticeReceipt.objects.create(
            notice=self.notice,
            participation=self.participation,
        )
        self.assertIsNone(receipt.acknowledged_at)

        response = self.client.post(
            reverse('vket:ack_notice', kwargs={'ack_token': str(receipt.ack_token)})
        )
        self.assertEqual(response.status_code, 200)

        receipt.refresh_from_db()
        self.assertIsNotNone(receipt.acknowledged_at)
        self.assertTrue(response.context['already_acked'])

    def test_ack_notice_view_shows_already_acked(self):
        """2回目のアクセスはalready_acked=Trueになる"""
        receipt = VketNoticeReceipt.objects.create(
            notice=self.notice,
            participation=self.participation,
            acknowledged_at=timezone.now(),
        )
        response = self.client.get(
            reverse('vket:ack_notice', kwargs={'ack_token': str(receipt.ack_token)})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['already_acked'])

    def test_notice_list_view_requires_login(self):
        """お知らせ一覧はログイン必須"""
        response = self.client.get(
            reverse('vket:notice_list', kwargs={'pk': self.collaboration.pk})
        )
        # ログイン画面にリダイレクト
        self.assertEqual(response.status_code, 302)

    def test_manage_notice_list_requires_staff(self):
        """管理用お知らせ一覧はstaff権限が必要"""
        self.client.login(username='owner_user2', password='testpass123')
        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 403)

    def test_manage_notice_list_shows_notice(self):
        """管理用お知らせ一覧にnoticeが表示される"""
        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.notice.title)

    def test_notice_create_auto_generates_receipts(self):
        """お知らせ作成時にactive参加者分のReceiptが自動生成される"""
        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_notice_create', kwargs={'pk': self.collaboration.pk}),
            data={
                'title': '自動生成テスト',
                'body': 'テスト本文',
                'target_scope': 'all',
                'requires_ack': 'on',
            },
        )
        self.assertEqual(response.status_code, 302)
        notice = VketNotice.objects.get(title='自動生成テスト')
        receipts = VketNoticeReceipt.objects.filter(notice=notice)
        self.assertEqual(receipts.count(), 1)
        self.assertEqual(receipts.first().participation, self.participation)

    def test_manage_notice_update_success(self):
        """superuserがお知らせのタイトルと本文を編集できる"""
        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_notice_update', kwargs={
                'pk': self.collaboration.pk,
                'notice_id': self.notice.pk,
            }),
            data={'title': '更新タイトル', 'body': '更新本文'},
        )
        self.assertEqual(response.status_code, 302)
        self.notice.refresh_from_db()
        self.assertEqual(self.notice.title, '更新タイトル')
        self.assertEqual(self.notice.body, '更新本文')

    def test_manage_notice_update_requires_staff(self):
        """一般ユーザー（非staff）はお知らせを編集できない"""
        self.client.login(username='owner_user2', password='testpass123')
        response = self.client.post(
            reverse('vket:manage_notice_update', kwargs={
                'pk': self.collaboration.pk,
                'notice_id': self.notice.pk,
            }),
            data={'title': '不正更新', 'body': '不正本文'},
        )
        self.assertEqual(response.status_code, 403)
        self.notice.refresh_from_db()
        self.assertEqual(self.notice.title, 'テストお知らせ')

    def test_manage_notice_update_validates_required_fields(self):
        """タイトル・本文が空の場合はエラー"""
        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_notice_update', kwargs={
                'pk': self.collaboration.pk,
                'notice_id': self.notice.pk,
            }),
            data={'title': '', 'body': ''},
        )
        self.assertEqual(response.status_code, 302)
        self.notice.refresh_from_db()
        self.assertEqual(self.notice.title, 'テストお知らせ')

    def test_manage_notice_update_rejects_other_collaboration_notice(self):
        """他のcollaborationのお知らせは編集できない（404）"""
        other_collab = VketCollaboration.objects.create(
            slug='other-collab',
            name='別コラボ',
            period_start=timezone.localdate(),
            period_end=timezone.localdate() + timedelta(days=7),
            registration_deadline=timezone.localdate() + timedelta(days=1),
            lt_deadline=timezone.localdate() + timedelta(days=3),
            phase=VketCollaboration.Phase.SCHEDULING,
        )
        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_notice_update', kwargs={
                'pk': other_collab.pk,
                'notice_id': self.notice.pk,
            }),
            data={'title': '不正更新', 'body': '不正本文'},
        )
        self.assertEqual(response.status_code, 404)

    def test_manage_notice_list_unacked_mentions_role(self):
        """未ACKのコミュニティのロールメンションがコンテキストに含まれる"""
        self.community.discord_mention_type = Community.DiscordMentionType.ROLE
        self.community.discord_mention_role_id = '111222333'
        self.community.save()

        VketNoticeReceipt.objects.create(
            notice=self.notice, participation=self.participation
        )

        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )
        stat = response.context['notice_stats'][0]
        self.assertIn('<@&111222333>', stat['unacked_mentions'])

    def test_manage_notice_list_unacked_mentions_users(self):
        """未ACKのコミュニティのユーザーメンションがコンテキストに含まれる"""
        self.community.discord_mention_type = Community.DiscordMentionType.USERS
        self.community.discord_mention_user_ids = ['aaa', 'bbb']
        self.community.save()

        VketNoticeReceipt.objects.create(
            notice=self.notice, participation=self.participation
        )

        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )
        stat = response.context['notice_stats'][0]
        self.assertIn('<@aaa>', stat['unacked_mentions'])
        self.assertIn('<@bbb>', stat['unacked_mentions'])

    def test_manage_notice_list_acked_not_in_mentions(self):
        """ACK済みのコミュニティはメンションに含まれない"""
        self.community.discord_mention_type = Community.DiscordMentionType.ROLE
        self.community.discord_mention_role_id = '999888777'
        self.community.save()

        VketNoticeReceipt.objects.create(
            notice=self.notice,
            participation=self.participation,
            acknowledged_at=timezone.now(),
        )

        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )
        stat = response.context['notice_stats'][0]
        self.assertEqual(stat['unacked_mentions'], [])

    def test_manage_notice_list_unacked_mentions_fallback_discord_id(self):
        """メンション未設定のコミュニティはメンバーのDiscord IDでメンション生成"""
        from allauth.socialaccount.models import SocialAccount
        SocialAccount.objects.create(
            user=self.owner, provider='discord', uid='123456789'
        )
        VketNoticeReceipt.objects.create(
            notice=self.notice, participation=self.participation
        )

        self.client.login(username='admin_user2', password='adminpass123')
        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )
        stat = response.context['notice_stats'][0]
        self.assertIn('<@123456789>', stat['unacked_mentions'])


class VketParticipationStatusTests(TestCase):
    """ParticipationStatusView のテスト（未確認お知らせバナー等）"""

    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(
            user_name='status_owner',
            email='status_owner@example.com',
            password='testpass123',
        )
        self.community = Community.objects.create(
            name='ステータステスト集会',
            status='approved',
            frequency='毎週',
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER,
        )

        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-2026-status-test',
            name='ステータステスト',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.SCHEDULING,
        )
        self.participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )

    def _set_active_community(self):
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

    def test_status_page_shows_unacked_count(self):
        """未確認のrequires_ackお知らせがあるとバナーに件数が表示される"""
        superuser = User.objects.create_superuser(
            user_name='su_status', email='su_status@example.com', password='p',
        )
        notice = VketNotice.objects.create(
            collaboration=self.collaboration,
            title='要確認',
            body='本文',
            requires_ack=True,
            target_scope=VketNotice.TargetScope.ALL_PARTICIPANTS,
            created_by=superuser,
        )
        VketNoticeReceipt.objects.create(
            notice=notice, participation=self.participation,
        )

        self.client.login(username='status_owner', password='testpass123')
        self._set_active_community()
        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['unacked_count'], 1)
        self.assertContains(response, '未確認のお知らせ')

    def test_status_page_unacked_count_zero_when_all_acked(self):
        """全レシートがACK済みならバナーは表示されない"""
        superuser = User.objects.create_superuser(
            user_name='su_status2', email='su_status2@example.com', password='p',
        )
        notice = VketNotice.objects.create(
            collaboration=self.collaboration,
            title='確認済み',
            body='本文',
            requires_ack=True,
            target_scope=VketNotice.TargetScope.ALL_PARTICIPANTS,
            created_by=superuser,
        )
        VketNoticeReceipt.objects.create(
            notice=notice,
            participation=self.participation,
            acknowledged_at=timezone.now(),
        )

        self.client.login(username='status_owner', password='testpass123')
        self._set_active_community()
        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['unacked_count'], 0)
        self.assertNotContains(response, '未確認のお知らせ')

    def test_status_page_without_participation(self):
        """participation がない場合 unacked_count=0"""
        other_user = User.objects.create_user(
            user_name='no_part_user', email='nopart@example.com', password='testpass123',
        )
        other_community = Community.objects.create(
            name='別の集会', status='approved', frequency='毎週',
        )
        CommunityMember.objects.create(
            community=other_community,
            user=other_user,
            role=CommunityMember.Role.OWNER,
        )

        self.client.login(username='no_part_user', password='testpass123')
        session = self.client.session
        session['active_community_id'] = other_community.id
        session.save()
        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['unacked_count'], 0)
        self.assertNotContains(response, '未確認のお知らせ')

    def test_stage_register_advances_progress(self):
        """ステージ登録POSTで progress が STAGE_REGISTERED に進む"""
        self.participation.progress = VketParticipation.Progress.APPLIED
        self.participation.save()

        self.client.login(username='status_owner', password='testpass123')
        self._set_active_community()
        response = self.client.post(
            reverse('vket:stage_register', kwargs={'pk': self.collaboration.pk}),
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        self.participation.refresh_from_db()
        self.assertEqual(self.participation.progress, VketParticipation.Progress.STAGE_REGISTERED)
        self.assertIsNotNone(self.participation.stage_registered_at)

    def test_stage_register_requires_applied(self):
        """APPLIED 以外ではステージ登録できない"""
        self.participation.progress = VketParticipation.Progress.REHEARSAL
        self.participation.save()

        self.client.login(username='status_owner', password='testpass123')
        self._set_active_community()
        response = self.client.post(
            reverse('vket:stage_register', kwargs={'pk': self.collaboration.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        self.participation.refresh_from_db()
        self.assertEqual(self.participation.progress, VketParticipation.Progress.REHEARSAL)

    def test_status_page_shows_stage_banner(self):
        """申請済み時にステージ登録バナーが表示される"""
        self.participation.progress = VketParticipation.Progress.APPLIED
        self.participation.save()

        self.client.login(username='status_owner', password='testpass123')
        self._set_active_community()
        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vketステージ登録')

    def test_status_page_shows_stage_registered_after_registration(self):
        """登録済み後も「登録済み」としてカードが表示される"""
        self.participation.progress = VketParticipation.Progress.STAGE_REGISTERED
        self.participation.stage_registered_at = timezone.now()
        self.participation.save()

        self.client.login(username='status_owner', password='testpass123')
        self._set_active_community()
        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vketステージ登録')
        self.assertContains(response, '登録済み')


class VketStatusRedirectViewTests(TestCase):
    """VketStatusRedirectView のテスト"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            user_name='redirect_user',
            email='redirect@example.com',
            password='testpass123',
        )
        self.superuser = User.objects.create_superuser(
            user_name='redirect_admin',
            email='redirect_admin@example.com',
            password='adminpass123',
        )
        self.community = Community.objects.create(
            name='リダイレクトテスト集会',
            status='approved',
            frequency='毎週',
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.user,
            role=CommunityMember.Role.OWNER,
        )

        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-2026-redirect-test',
            name='リダイレクトテスト',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.ENTRY_OPEN,
        )

    def test_redirect_requires_login(self):
        """未ログインで302（ログインページへ）"""
        response = self.client.get(reverse('vket:status_redirect'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_redirect_to_latest_collaboration(self):
        """ログインユーザーで最新コラボのstatusへリダイレクト"""
        self.client.login(username='redirect_user', password='testpass123')
        response = self.client.get(reverse('vket:status_redirect'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse('vket:status', kwargs={'pk': self.collaboration.pk}),
        )

    def test_redirect_to_list_when_only_archived(self):
        """ARCHIVEDのみの場合、公開一覧にリダイレクト"""
        self.collaboration.phase = VketCollaboration.Phase.ARCHIVED
        self.collaboration.save()

        self.client.login(username='redirect_user', password='testpass123')
        response = self.client.get(reverse('vket:status_redirect'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('vket:list'))

    def test_redirect_skips_draft_for_non_superuser(self):
        """非superuserでDRAFTのみの場合、公開一覧にリダイレクト"""
        self.collaboration.phase = VketCollaboration.Phase.DRAFT
        self.collaboration.save()

        self.client.login(username='redirect_user', password='testpass123')
        response = self.client.get(reverse('vket:status_redirect'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('vket:list'))

    def test_superuser_can_access_draft(self):
        """superuserはDRAFTコラボにもリダイレクトされる"""
        self.collaboration.phase = VketCollaboration.Phase.DRAFT
        self.collaboration.save()

        self.client.login(username='redirect_admin', password='adminpass123')
        response = self.client.get(reverse('vket:status_redirect'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse('vket:status', kwargs={'pk': self.collaboration.pk}),
        )

    def test_status_page_shows_collaboration_dropdown(self):
        """コラボ切替ドロップダウンが status ページに表示される"""
        today = timezone.localdate()
        second = VketCollaboration.objects.create(
            slug='vket-2026-redirect-test-2',
            name='リダイレクトテスト2',
            period_start=today - timedelta(days=30),
            period_end=today - timedelta(days=23),
            registration_deadline=today - timedelta(days=29),
            lt_deadline=today - timedelta(days=27),
            phase=VketCollaboration.Phase.LOCKED,
        )

        self.client.login(username='redirect_user', password='testpass123')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()
        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '切替')
        self.assertContains(response, second.name)

    def test_status_page_shows_admin_link_for_superuser(self):
        """superuserで管理者リンク（管理画面）が表示される"""
        # superuserにもCommunityMembershipが必要
        CommunityMember.objects.create(
            community=self.community,
            user=self.superuser,
            role=CommunityMember.Role.OWNER,
        )
        self.client.login(username='redirect_admin', password='adminpass123')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        # participationを作成（管理者リンクはアクションボタンエリアに表示）
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )

        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'fa-gear')

    def test_status_page_hides_admin_link_for_normal_user(self):
        """一般ユーザーには管理者リンクが表示されない"""
        self.client.login(username='redirect_user', password='testpass123')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )

        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'fa-gear')


class VketPublishViewTests(TestCase):
    """ManagePublishViewのテスト"""

    def setUp(self):
        self.client = Client()
        cache.clear()
        self.superuser = User.objects.create_superuser(
            user_name='admin_pub',
            email='admin_pub@example.com',
            password='adminpass123',
        )
        self.community = Community.objects.create(
            name='公開テスト集会',
            status='approved',
            frequency='毎週',
        )
        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-2026-publish-test',
            name='公開テスト',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.LOCKED,
        )
        self.participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
            confirmed_date=today,
            confirmed_start_time='21:00',
            confirmed_duration=60,
        )

    def tearDown(self):
        cache.clear()

    def test_publish_creates_event_and_updates_participation(self):
        """公開処理でEventが作成されpublished_eventが紐づく"""
        self.client.login(username='admin_pub', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}),
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        self.participation.refresh_from_db()
        self.assertIsNotNone(self.participation.published_event_id)
        self.assertEqual(self.participation.progress, VketParticipation.Progress.DONE)

        event = self.participation.published_event
        self.assertEqual(event.community, self.community)
        self.assertEqual(event.start_time.strftime('%H:%M'), '21:00')
        self.assertEqual(event.duration, 60)

    def test_publish_skips_declined_participation(self):
        """不参加の参加情報は公開同期の対象外になる"""
        declined_community = Community.objects.create(
            name='不参加テスト集会',
            status='approved',
            frequency='毎週',
        )
        declined_participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=declined_community,
            lifecycle=VketParticipation.Lifecycle.DECLINED,
            confirmed_date=self.collaboration.period_start,
            confirmed_start_time='22:00',
            confirmed_duration=60,
        )
        VketPresentation.objects.create(
            participation=declined_participation,
            order=0,
            speaker='不参加登壇者',
            theme='不参加テーマ',
            requested_start_time=time(22, 30),
            status=VketPresentation.Status.CONFIRMED,
        )

        self.client.login(username='admin_pub', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}),
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        declined_participation.refresh_from_db()
        self.assertIsNone(declined_participation.published_event_id)
        self.assertFalse(Event.objects.filter(community=declined_community).exists())
        self.assertFalse(EventDetail.objects.filter(speaker='不参加登壇者').exists())

    def test_publish_is_forbidden_when_not_locked(self):
        """LOCKEDフェーズ以外では公開処理が403になる"""
        self.collaboration.phase = VketCollaboration.Phase.SCHEDULING
        self.collaboration.save()

        self.client.login(username='admin_pub', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}),
        )
        self.assertEqual(response.status_code, 403)

    def test_publish_is_idempotent(self):
        """公開処理を2回実行しても同じEventが使われ、重複作成されない"""
        from event.models import Event

        self.client.login(username='admin_pub', password='adminpass123')
        url = reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk})

        # 1回目
        self.client.post(url)
        self.participation.refresh_from_db()
        first_event_id = self.participation.published_event_id
        self.assertIsNotNone(first_event_id)

        # 2回目
        self.client.post(url)
        self.participation.refresh_from_db()
        # 同じEventが使われていること
        self.assertEqual(self.participation.published_event_id, first_event_id)
        # Eventが重複作成されていないこと
        self.assertEqual(Event.objects.filter(community=self.community).count(), 1)

    def test_lt_start_time_flows_to_event_detail(self):
        """requested_start_time が EventDetail.start_time に反映される"""
        # プレゼンテーションに requested_start_time を設定（CONFIRMED で公開対象にする）
        VketPresentation.objects.create(
            participation=self.participation,
            order=0,
            speaker='テスト登壇者',
            theme='テストテーマ',
            requested_start_time=time(21, 30),
            status=VketPresentation.Status.CONFIRMED,
        )

        self.client.login(username='admin_pub', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}),
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        self.participation.refresh_from_db()
        event = self.participation.published_event
        self.assertIsNotNone(event)

        # EventDetail が作成され、start_time に requested_start_time が使われていること
        detail = EventDetail.objects.get(event=event)
        self.assertEqual(detail.start_time.strftime('%H:%M'), '21:30')

    def test_publish_clears_index_cache_when_updating_existing_event_detail(self):
        """公開処理で既存EventDetailをbulk updateした場合もトップページキャッシュを削除する"""
        event = Event.objects.create(
            community=self.community,
            date=self.collaboration.period_start,
            start_time='21:00',
            duration=60,
            weekday='Fri',
        )
        self.participation.published_event = event
        self.participation.save(update_fields=['published_event', 'updated_at'])
        detail = EventDetail.objects.create(
            event=event,
            detail_type='LT',
            speaker='更新前登壇者',
            theme='更新前テーマ',
            start_time='21:00',
            status='approved',
        )
        VketPresentation.objects.create(
            participation=self.participation,
            order=0,
            speaker='更新後登壇者',
            theme='更新後テーマ',
            requested_start_time=time(21, 30),
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=detail,
        )
        cache_key = get_index_view_cache_key()
        cache.set(cache_key, {'upcoming_event_details': ['stale']}, 60)

        self.client.login(username='admin_pub', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}),
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        detail.refresh_from_db()
        self.assertEqual(detail.speaker, '更新後登壇者')
        self.assertEqual(detail.start_time.strftime('%H:%M'), '21:30')
        self.assertIsNone(cache.get(cache_key))
