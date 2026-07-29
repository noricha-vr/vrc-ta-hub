"""Vketコラボ機能のテスト."""

from datetime import time

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from vket.models import (
    VketParticipation,
    VketPresentation,
)
from vket.views.helpers import _build_schedule_context


User = get_user_model()


from ._vket_test_bases import VketApplyFlowBase


class VketApplyFlowTests(VketApplyFlowBase):
    def test_status_page_shows_register_complete_guidance(self):
        """参加状況画面で登録後の次アクションが明示される"""
        self.collaboration.slug = 'vket-2026-summer'
        self.collaboration.save(update_fields=['slug'])
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
            'Vket公式サイトのステージページに、イベント情報を暫定内容で登録してください。',
        )
        self.assertContains(
            response,
            '日程や発表内容が未確定でも、後から更新できます。',
        )
        self.assertContains(
            response,
            '登録が終わったら、この画面に戻って「登録完了」を押してください。',
        )
        self.assertContains(
            response,
            '開催会場は「Parareal Central Ignition Point - 着火点 - - エントランス」に設定してください。',
        )
        self.assertContains(response, 'タグは「Vketステージ」を選択してください。')
        self.assertContains(
            response,
            'Vket側で登録しただけでは Hub の進捗は更新されません。',
        )
        self.assertContains(
            response,
            'https://vket.com/hub/2026Summer/notification',
        )
        self.assertContains(response, 'Vketステージに登録する')
        self.assertNotContains(response, '参加申込みが完了しました。')

        body = response.content.decode()
        self.assertLess(
            body.index('Vketステージに登録する'),
            body.index('<i class="fas fa-check me-1"></i>登録完了'),
        )

    def test_status_page_uses_collaboration_stage_url_when_configured(self):
        """コラボに登録URLが設定されている場合はそのURLを使う"""
        custom_stage_url = 'https://example.com/vket/custom-stage'
        self.collaboration.settings_json = {'stage_url': f' {custom_stage_url} '}
        self.collaboration.save(update_fields=['settings_json'])
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
        self.assertContains(response, custom_stage_url)
        self.assertNotContains(response, 'https://vket.com/hub/2026Summer/notification')
        self.assertNotContains(response, 'Parareal Central Ignition Point - 着火点 - - エントランス')

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

    def test_schedule_table_uses_unpublished_presentation_start_time(self):
        """未公開の発表開始時刻が日程表のLTマーカーに反映される"""
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time=time(21, 0),
            requested_duration=60,
            progress=VketParticipation.Progress.STAGE_REGISTERED,
        )
        VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='未定',
            theme='未定',
            requested_start_time=time(21, 30),
            duration=30,
        )

        context = _build_schedule_context(self.collaboration, include_requested=True)
        row = next(
            r for r in context['rows'] if r['participation'].pk == participation.pk
        )
        lt_tooltips = [
            cell['lt_tooltip'] for cell in row['cells'] if cell['lt_times']
        ]

        self.assertEqual(row['start_time'], time(21, 0))
        self.assertEqual(lt_tooltips, ['21:30'])

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
