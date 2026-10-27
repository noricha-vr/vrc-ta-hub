"""参加者ガイドの撮影対象を、デモデータの描画結果で照合する。"""
import os
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest import mock

from bs4 import BeautifulSoup
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from event.models import EventDetail
from event.services.speaker_invite import create_invite_token
from guide.demo import DEMO_USERS
from user_account.models import CustomUser


@override_settings(DEBUG=True, GUIDE_CAPTURE_EXTENDED_DEMO=True)
class ParticipantScenarioTargetsTest(TestCase):
    @mock.patch.dict(os.environ, {'GUIDE_DEMO_PASSWORD': 'guide-demo-test-only'})
    @mock.patch('django.utils.timezone.now', return_value=datetime(2030, 4, 1, 3, tzinfo=timezone.utc))
    def test_participant_capture_targets(self, _now):
        demo_root = str(Path(settings.DATABASES['default']['NAME']).resolve().parent)
        with (
            override_settings(GUIDE_DEMO_ROOT=demo_root),
            mock.patch('event.notifications.post_discord_webhook'),
            self.captureOnCommitCallbacks(execute=True),
        ):
            call_command('seed_guide_demo', stdout=StringIO())
        self.client.force_login(CustomUser.objects.get(email=DEMO_USERS['owner'][1]))

        def page(path):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            return BeautifulSoup(response.content, 'html.parser')

        def unique(soup, *selectors):
            for selector in selectors:
                with self.subTest(selector=selector):
                    self.assertEqual(len(soup.select(selector)), 1, selector)

        soup = page('/event/my_list/?community=1')
        unique(soup, 'nav.mylist-nav', 'nav[aria-label="個人用"]',
               'nav[aria-label="個人用"] a[href="/account/lt-applications/"]',
               'nav.mylist-nav a[href^="/event/detail/history/?community_name="]')
        self.assertIsNone(soup.select_one('nav.mylist-nav a[href="/account/lt-applications/"]'))
        self.assertEqual(soup.select_one('nav[aria-label="個人用"] a').get_text(strip=True), '自分の発表申請')

        soup = page('/vket/1/apply/')
        unique(soup, '#schedule', '#id_requested_date', '.row:has(#id_requested_start_time)',
               '#id_requested_start_time', '#id_requested_duration', '#lt-formset-container',
               '#id_lt-0-speaker', '#id_lt-0-theme', '#id_lt-0-lt_start_time',
               '#organizer-note', '#id_organizer_note', '.admin-action-bar button[form="vket-apply-form"]')
        self.assertEqual(soup.select_one('#schedule h2').get_text(strip=True), '参加日程の希望（締切1）')
        self.assertEqual(soup.select_one('#organizer-note h2').get_text(strip=True), '運営への連絡')
        self.assertEqual(soup.select_one('button[form="vket-apply-form"]').get_text(strip=True), '保存する')

        soup = page('/vket/1/status/')
        # 「確認する」の部分一致ではステージ登録のリンクも拾ってしまう。
        unique(soup, '#member-attention', '#member-attention a[href^="/vket/1/notices/?open="]',
               '#member-attention a[href="#stage-registration"]',
               '.admin-card:has(h2:-soup-contains("日程情報")) a[href="/vket/1/apply/"]',
               '#stage-registration', '[data-confirm-panel="stage-register-confirm"]',
               '#stage-register-confirm button[type="submit"]',
               '#stage-register-confirm [data-cancel-panel]', 'nav a[href="/vket/1/notices/"]')
        self.assertEqual(len(soup.select('#stage-registration ol > li')), 3)
        self.assertEqual(soup.select_one('[data-confirm-panel=stage-register-confirm]').text, '登録したことを記録する')
        copy = soup.select_one('#stage-registration a[href="/vket/1/stage-copy/1/"]')
        self.assertIsNotNone(copy)
        self.assertEqual(copy.text, 'コピー用ページをもう一度開く')
        self.assertIn('btn-primary', soup.select_one('#stage-register-confirm button[type=submit]')['class'])
        self.assertIn('hidden', soup.select_one('#stage-register-confirm').attrs)
        self.assertEqual(soup.select_one('#stage-register-confirm button[type="submit"]').get_text(strip=True),
                         '登録完了を記録する')

        soup = page('/vket/1/notices/?open=1')
        unique(soup, '#unackedAccordion', '#unackedAccordion button[type="submit"]',
               '#unackedAccordion #notice-1.show')
        self.assertEqual(soup.select_one('#unackedAccordion button[type="submit"]').get_text(strip=True),
                         '確認しました')
        soup = page('/vket/1/notices/?open=2')
        unique(soup, '.notice-done', '.notice-done .accordion-button:-soup-contains("資料の準備ができました")',
               '.notice-done #notice-2.show')
        self.assertIsNone(soup.select_one('.notice-done button[type="submit"]'))

        for path in ('/vket/1/stage-copy/1/', '/vket/1/stage-copy/2/'):
            if path.endswith('/2/'):
                self.client.force_login(CustomUser.objects.get(email=DEMO_USERS['staff'][1]))
            soup = page(path)
            for field in ('title', 'body', 'start'):
                unique(soup, f'.admin-card:has(#stage-{field})', f'button[data-copy-target="stage-{field}"]')
            unique(soup, '#stage-thumbnail', 'nav.mylist-nav a[href="/vket/1/status/"]')
            if path.endswith('/1/'):
                unique(soup, '#stage-thumbnail a[href$="/thumbnail/download/"]')
            else:
                unique(soup, '#stage-thumbnail-form button[type="submit"]')

        self.client.logout()
        soup = page('/vket/ack/00000000-0000-4000-8000-000000000001/')
        unique(soup, 'main .container', 'main button[type="submit"]')
        self.assertEqual(soup.select_one('button[type="submit"]').get_text(strip=True), '確認しました')

        self.client.force_login(CustomUser.objects.get(email=DEMO_USERS['speaker'][1]))
        linked = EventDetail.objects.get(theme='小さな実験を始める', applicant__isnull=False)
        soup = page('/event/detail/history/?mine=1')
        unique(soup, 'section[aria-labelledby="mine-filter-heading"]', 'input[name="q"]',
               'section[aria-labelledby="mine-presentations-heading"]',
               f'article.mine-presentation:has(a[href="/event/detail/{linked.pk}/update/"])',
               f'a[href="/event/detail/{linked.pk}/update/"]',
               'nav[aria-label="個人用"] a[href="/account/lt-applications/"]')

        unlinked = EventDetail.objects.get(theme='小さな実験を始める', applicant__isnull=True)
        token = create_invite_token(unlinked)
        response = self.client.post(reverse('event:speaker_invite_token_exchange'), {'token': token})
        self.assertEqual(response.status_code, 200)
        soup = page(reverse('event:speaker_link_confirm'))
        unique(soup, '.speaker-link .admin-page-header', '.speaker-link .admin-card',
               '#speaker-link-confirm-button', '#speaker-link-heading',
               f'.speaker-link-actions a[href="/event/detail/{unlinked.pk}/"]')
        self.assertEqual(soup.select_one('#speaker-link-heading').get_text(strip=True), '紐づけ内容の確認')
        self.assertIn('今は紐づけない', soup.select_one('.speaker-link-actions a').get_text())
