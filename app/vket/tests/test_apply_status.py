"""Vketコラボ機能のテスト."""


from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from vket.models import (
    VketParticipation,
)


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

        self.client.force_login(self.owner)
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

        self.client.force_login(self.owner)
        self._set_active_community()

        response = self.client.get(reverse('vket:status', kwargs={'pk': self.collaboration.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, custom_stage_url)
        self.assertNotContains(response, 'https://vket.com/hub/2026Summer/notification')
        self.assertNotContains(response, 'Parareal Central Ignition Point - 着火点 - - エントランス')
