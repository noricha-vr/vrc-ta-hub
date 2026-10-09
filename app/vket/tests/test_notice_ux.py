"""お知らせの確認導線（一覧からの確認・並び順・リマインド文・二重作成防止）のテスト."""

from datetime import timedelta

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from community.models import CommunityMember
from tests.factories import make_community, make_community_member, make_user
from vket.models import (
    VketCollaboration,
    VketNotice,
    VketNoticeReceipt,
    VketParticipation,
)
from vket.views.notice import NOTICE_DUPLICATE_WINDOW_SECONDS


def _messages(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


class NoticeUxTestBase(TestCase):
    def setUp(self):
        self.admin = make_user(
            user_name='notice_ux_admin',
            email='notice_ux_admin@example.com',
            is_staff=True,
            is_superuser=True,
        )
        self.owner = make_user(user_name='notice_ux_owner', email='notice_ux_owner@example.com')
        self.community = make_community(name='自分の集会', owner=self.owner)

        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-notice-ux',
            name='お知らせ導線テスト',
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
        self.list_url = reverse('vket:notice_list', kwargs={'pk': self.collaboration.pk})

    def _make_receipt(self, title, requires_ack=True, acked=False, participation=None, minutes_ago=0):
        notice = VketNotice.objects.create(
            collaboration=self.collaboration,
            title=title,
            body=f'{title}の本文',
            requires_ack=requires_ack,
            created_by=self.admin,
        )
        receipt = VketNoticeReceipt.objects.create(
            notice=notice,
            participation=participation or self.participation,
            acknowledged_at=timezone.now() if acked else None,
        )
        created_at = timezone.now() - timedelta(minutes=minutes_ago)
        VketNotice.objects.filter(pk=notice.pk).update(created_at=created_at)
        VketNoticeReceipt.objects.filter(pk=receipt.pk).update(created_at=created_at)
        receipt.refresh_from_db()
        return receipt

    def _ack_url(self, receipt):
        return reverse(
            'vket:notice_list_ack',
            kwargs={'pk': self.collaboration.pk, 'receipt_id': receipt.pk},
        )


class NoticeListAckTests(NoticeUxTestBase):
    """項目1・3: 一覧の中で確認を終え、次の未確認へ進む"""

    def test_list_shows_ack_form_for_unacked_notice(self):
        receipt = self._make_receipt('未確認のお知らせ')
        self.client.force_login(self.owner)

        response = self.client.get(self.list_url)

        self.assertContains(response, f'action="{self._ack_url(receipt)}"')
        self.assertContains(response, 'csrfmiddlewaretoken')

    def test_ack_from_list_marks_receipt_and_opens_next_unacked(self):
        older = self._make_receipt('古い未確認', minutes_ago=10)
        newer = self._make_receipt('新しい未確認', minutes_ago=1)
        self.client.force_login(self.owner)

        response = self.client.post(self._ack_url(newer))

        self.assertRedirects(response, f'{self.list_url}?open={older.notice_id}')
        newer.refresh_from_db()
        self.assertIsNotNone(newer.acknowledged_at)
        self.assertEqual(newer.acknowledged_by, self.owner)
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.last_acknowledged_by, self.owner)
        self.assertIn('確認しました。残り 1 件です。', _messages(response))

    def test_ack_last_unacked_says_none_left(self):
        receipt = self._make_receipt('最後の未確認')
        self.client.force_login(self.owner)

        response = self.client.post(self._ack_url(receipt), follow=True)

        self.assertContains(response, '確認しました。未確認のお知らせはもうありません。')
        self.assertContains(response, '未確認のお知らせはありません')
        self.assertEqual(response.context['unacked_receipts'], [])

    def test_ack_from_list_is_idempotent(self):
        receipt = self._make_receipt('二度押し')
        self.client.force_login(self.owner)
        self.client.post(self._ack_url(receipt))
        receipt.refresh_from_db()
        first_acked_at = receipt.acknowledged_at

        response = self.client.post(self._ack_url(receipt))

        self.assertEqual(response.status_code, 302)
        receipt.refresh_from_db()
        self.assertEqual(receipt.acknowledged_at, first_acked_at)
        self.assertIn('このお知らせは前に確認済みです。', _messages(response))

    def test_cannot_ack_other_community_receipt(self):
        other_owner = make_user(user_name='notice_ux_other', email='notice_ux_other@example.com')
        other_community = make_community(name='よその集会', owner=other_owner)
        other_participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=other_community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )
        other_receipt = self._make_receipt('よその未確認', participation=other_participation)
        self.client.force_login(self.owner)

        response = self.client.post(self._ack_url(other_receipt))

        self.assertEqual(response.status_code, 404)
        other_receipt.refresh_from_db()
        self.assertIsNone(other_receipt.acknowledged_at)

    def test_cannot_ack_receipt_via_other_collaboration_url(self):
        receipt = self._make_receipt('別コラボURL')
        other_collab = VketCollaboration.objects.create(
            slug='vket-notice-ux-other',
            name='別コラボ',
            period_start=self.collaboration.period_start,
            period_end=self.collaboration.period_end,
            registration_deadline=self.collaboration.registration_deadline,
            lt_deadline=self.collaboration.lt_deadline,
            phase=VketCollaboration.Phase.SCHEDULING,
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse('vket:notice_list_ack', kwargs={'pk': other_collab.pk, 'receipt_id': receipt.pk})
        )

        self.assertEqual(response.status_code, 404)
        receipt.refresh_from_db()
        self.assertIsNone(receipt.acknowledged_at)

    def test_ack_from_list_rejects_get_and_anonymous(self):
        receipt = self._make_receipt('GETでは確認しない')

        anonymous = self.client.post(self._ack_url(receipt))
        self.client.force_login(self.owner)
        get_response = self.client.get(self._ack_url(receipt))

        self.assertEqual(anonymous.status_code, 302)
        self.assertIn('/login', anonymous['Location'])
        self.assertEqual(get_response.status_code, 405)
        receipt.refresh_from_db()
        self.assertIsNone(receipt.acknowledged_at)


class NoticeListOrderingTests(NoticeUxTestBase):
    """項目2: 未確認を上に、件数を見出しに、最初の未確認を開く"""

    def test_unacked_first_with_count_heading_and_first_unacked_open(self):
        acked = self._make_receipt('確認済みのお知らせ', acked=True, minutes_ago=1)
        info = self._make_receipt('確認不要のお知らせ', requires_ack=False, minutes_ago=2)
        unacked_new = self._make_receipt('新しい未確認', minutes_ago=3)
        unacked_old = self._make_receipt('古い未確認', minutes_ago=4)
        self.client.force_login(self.owner)

        response = self.client.get(self.list_url)

        self.assertEqual(
            [r.pk for r in response.context['unacked_receipts']], [unacked_new.pk, unacked_old.pk]
        )
        self.assertEqual([r.pk for r in response.context['other_receipts']], [acked.pk, info.pk])
        self.assertEqual(response.context['open_notice_id'], unacked_new.notice_id)
        self.assertContains(response, '未確認 2 件')
        self.assertContains(response, '確認済み・確認不要 2 件')
        self.assertContains(
            response, f'id="notice-{unacked_new.notice_id}" class="accordion-collapse collapse show"'
        )
        self.assertContains(
            response, f'id="notice-{unacked_old.notice_id}" class="accordion-collapse collapse"'
        )

    def test_open_param_overrides_default_open(self):
        self._make_receipt('未確認')
        acked = self._make_receipt('確認済み', acked=True)
        self.client.force_login(self.owner)

        response = self.client.get(f'{self.list_url}?open={acked.notice_id}')

        self.assertEqual(response.context['open_notice_id'], acked.notice_id)
        self.assertTrue(response.context['scroll_to_open'])

    def test_invalid_open_param_falls_back_to_first_unacked(self):
        unacked = self._make_receipt('未確認')
        self.client.force_login(self.owner)

        response = self.client.get(f'{self.list_url}?open=abc')

        self.assertEqual(response.context['open_notice_id'], unacked.notice_id)
        self.assertFalse(response.context['scroll_to_open'])


class AckTokenFlowTests(NoticeUxTestBase):
    """項目3: 確認用 URL からの確認は PRG にし、今押したか前に確認済みかを言い分ける"""

    def _token_url(self, receipt, query=''):
        return reverse('vket:ack_notice', kwargs={'ack_token': receipt.ack_token}) + query

    def test_anonymous_post_redirects_to_done_page(self):
        receipt = self._make_receipt('トークン確認')

        response = self.client.post(self._token_url(receipt))

        self.assertRedirects(response, self._token_url(receipt, '?done=1'))
        followed = self.client.get(response['Location'])
        self.assertTrue(followed.context['just_acked'])
        self.assertContains(followed, '<h1 class="h3 fw-bold mb-2">確認しました</h1>', html=True)

    def test_previously_acked_get_says_already_acked(self):
        receipt = self._make_receipt('前に確認', acked=True)

        response = self.client.get(self._token_url(receipt))

        self.assertFalse(response.context['just_acked'])
        self.assertContains(response, '<h1 class="h3 fw-bold mb-2">確認済みです</h1>', html=True)
        self.assertNotContains(response, 'すでに確認済み')

    def test_previously_acked_post_does_not_claim_just_acked(self):
        receipt = self._make_receipt('前に確認', acked=True)

        response = self.client.post(self._token_url(receipt))

        self.assertRedirects(response, self._token_url(receipt))

    def test_done_param_on_unacked_receipt_does_not_change_state(self):
        receipt = self._make_receipt('GETは状態を変えない')

        response = self.client.get(self._token_url(receipt, '?done=1'))

        self.assertFalse(response.context['just_acked'])
        receipt.refresh_from_db()
        self.assertIsNone(receipt.acknowledged_at)

    def test_member_post_redirects_to_list_with_remaining(self):
        other = self._make_receipt('もう1件の未確認', minutes_ago=5)
        receipt = self._make_receipt('トークン確認', minutes_ago=1)
        self.client.force_login(self.owner)

        response = self.client.post(self._token_url(receipt))

        self.assertRedirects(response, f'{self.list_url}?open={other.notice_id}')
        self.assertIn('確認しました。残り 1 件です。', _messages(response))


class ManageNoticeCreateDuplicateTests(NoticeUxTestBase):
    """項目4: 同じ人が数秒以内に同じタイトルで作ったら 2 件目を作らない"""

    def _post(self, title='二重作成テスト'):
        return self.client.post(
            reverse('vket:manage_notice_create', kwargs={'pk': self.collaboration.pk}),
            data={'title': title, 'body': '本文', 'target_scope': 'all', 'requires_ack': '1'},
        )

    def test_second_create_within_window_is_skipped(self):
        self.client.force_login(self.admin)

        self._post()
        response = self._post()

        self.assertRedirects(
            response, reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(VketNotice.objects.filter(title='二重作成テスト').count(), 1)
        self.assertEqual(VketNoticeReceipt.objects.filter(notice__title='二重作成テスト').count(), 1)
        self.assertIn(
            '同じタイトルのお知らせを作成したばかりのため、もう1件は作りませんでした。',
            _messages(response),
        )

    def test_same_title_after_window_is_created(self):
        self.client.force_login(self.admin)
        self._post()
        VketNotice.objects.filter(title='二重作成テスト').update(
            created_at=timezone.now() - timedelta(seconds=NOTICE_DUPLICATE_WINDOW_SECONDS + 1)
        )

        self._post()

        self.assertEqual(VketNotice.objects.filter(title='二重作成テスト').count(), 2)

    def test_different_title_within_window_is_created(self):
        self.client.force_login(self.admin)

        self._post('一件目')
        self._post('二件目')

        self.assertEqual(VketNotice.objects.filter(title__in=['一件目', '二件目']).count(), 2)

    def test_create_and_edit_forms_disable_button_on_submit(self):
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )

        self.assertContains(response, 'class="js-submit-once" data-loading-text="作成中…"')
        self.assertContains(response, 'class="js-submit-once" data-loading-text="更新中…"')


class ManageNoticeListReminderTests(NoticeUxTestBase):
    """項目5・6 と配信対象の文言"""

    def setUp(self):
        super().setUp()
        self.community.discord_mention_type = self.community.DiscordMentionType.ROLE
        self.community.discord_mention_role_id = '555666777'
        self.community.save()
        self.second_community = make_community(name='二つ目の集会')
        make_community_member(self.second_community, self.owner, role=CommunityMember.Role.STAFF)
        self.second_participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.second_community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )
        self.receipt = self._make_receipt('リマインド対象')
        VketNoticeReceipt.objects.create(
            notice=self.receipt.notice,
            participation=self.second_participation,
            acknowledged_at=timezone.now(),
        )
        self.client.force_login(self.admin)

    def _get(self):
        return self.client.get(
            reverse('vket:manage_notice_list', kwargs={'pk': self.collaboration.pk})
        )

    def test_remind_text_is_ready_to_paste(self):
        response = self._get()

        stat = response.context['notice_stats'][0]
        expected_url = f'http://testserver{self.list_url}?open={self.receipt.notice_id}'
        self.assertEqual(
            stat['remind_text'], f'<@&555666777>\n【要確認】リマインド対象\n{expected_url}'
        )
        self.assertContains(response, '未確認 1 集会にリマインド文をコピー')
        self.assertContains(response, 'id="copyToast"')

    def test_unacked_count_is_primary_and_names_are_listed(self):
        response = self._get()

        stat = response.context['notice_stats'][0]
        self.assertEqual(stat['unacked'], 1)
        self.assertEqual(stat['acked_percent'], 50)
        self.assertContains(response, '未確認 1</span>')
        self.assertContains(response, 'role="progressbar"')
        self.assertContains(
            response,
            '<li><i class="fas fa-circle-exclamation text-warning me-1"></i>自分の集会</li>',
            html=True,
        )
        self.assertNotContains(response, 'ACK状況')

    def test_unacked_names_preview_is_truncated(self):
        for i in range(4):
            community = make_community(name=f'追加集会{i}')
            participation = VketParticipation.objects.create(
                collaboration=self.collaboration,
                community=community,
                lifecycle=VketParticipation.Lifecycle.ACTIVE,
            )
            VketNoticeReceipt.objects.create(notice=self.receipt.notice, participation=participation)

        response = self._get()

        stat = response.context['notice_stats'][0]
        self.assertEqual(len(stat['unacked_names_preview']), 3)
        self.assertEqual(stat['unacked_names_rest'], 2)
        self.assertContains(response, 'ほか 2 集会')

    def test_scope_label_and_ack_wording_are_accurate(self):
        VketNotice.objects.filter(pk=self.receipt.notice_id).update(
            target_scope=VketNotice.TargetScope.UNACKED
        )

        response = self._get()

        self.assertEqual(
            response.context['notice_stats'][0]['scope_label'], 'まだ一度も確認していない参加者'
        )
        self.assertNotContains(response, '未確認者のみ')
        self.assertNotContains(response, 'ACKリンク')
        self.assertContains(response, '<option value="unacked">まだ一度も確認していない参加者</option>')


class ParticipationStatusUnackedLinkTests(NoticeUxTestBase):
    """参加状況の未確認の警告から最初の未確認を直接開く"""

    def test_warning_links_to_first_unacked(self):
        self._make_receipt('古い未確認', minutes_ago=10)
        newest = self._make_receipt('新しい未確認', minutes_ago=1)
        self.client.force_login(self.owner)

        response = self.client.get(reverse('vket:status', kwargs={'pk': self.collaboration.pk}))

        self.assertEqual(response.context['first_unacked_notice_id'], newest.notice_id)
        self.assertContains(
            response,
            f'href="{self.list_url}?open={newest.notice_id}"\n'
            '                   class="btn btn-sm btn-warning ms-2 text-nowrap flex-shrink-0"',
        )
