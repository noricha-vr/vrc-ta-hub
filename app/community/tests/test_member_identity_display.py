"""同名メンバーの識別表示とメールアドレス非露出の検証。

user_name の unique 制約解除（#579）により、メンバー管理画面で完全に同じ表示名の
ユーザーが並び得る。権限操作（削除・昇格）で別人を誤操作しないための副次情報が
出ていること、その副次情報に email が含まれないことを振る舞いとして検証する。
"""
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from community.models import CommunityMember
from tests.factories import (
    make_community,
    make_community_member,
    make_discord_linked_user,
    make_user,
)

DUPLICATE_NAME = 'かぶり太郎'


class SameNameMemberIdentificationTest(TestCase):
    """同名ユーザーが視覚的に区別できることの検証"""

    def setUp(self):
        self.client = Client()
        self.owner = make_user(user_name='オーナー', email='owner@example.com')
        # 表示名が完全一致する2人（片方だけ Discord 連携あり）
        self.duplicate_linked = make_discord_linked_user(
            user_name=DUPLICATE_NAME, email='dup-linked@example.com'
        )
        self.duplicate_unlinked = make_user(
            user_name=DUPLICATE_NAME, email='dup-unlinked@example.com'
        )

        self.community = make_community(name='テスト集会', owner=self.owner)
        for user in (self.duplicate_linked, self.duplicate_unlinked):
            make_community_member(
                self.community, user, role=CommunityMember.Role.STAFF
            )

        self.url = reverse(
            'community:member_manage', kwargs={'pk': self.community.pk}
        )

    def test_same_name_members_are_distinguishable(self):
        """同名メンバーの行に Discord 連携有無の差分が出る"""
        self.client.force_login(self.owner)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        members = {m.user_id: m for m in response.context['members']}
        self.assertTrue(members[self.duplicate_linked.pk].has_discord_link)
        self.assertFalse(members[self.duplicate_unlinked.pk].has_discord_link)

        content = response.content.decode()
        self.assertIn('Discord連携あり', content)
        self.assertIn('Discord連携なし', content)

    def test_registration_date_is_rendered_for_each_member(self):
        """各メンバーの登録日が描画され、副次情報として使える"""
        joined = self.duplicate_unlinked.date_joined.replace(
            year=2020, month=1, day=15
        )
        self.duplicate_unlinked.date_joined = joined
        self.duplicate_unlinked.save(update_fields=['date_joined'])
        self.client.force_login(self.owner)

        response = self.client.get(self.url)

        self.assertContains(response, '2020/01/15')

    def test_member_emails_are_not_exposed(self):
        """他メンバーのメールアドレスはレスポンスに現れない"""
        self.client.force_login(self.owner)

        response = self.client.get(self.url)

        content = response.content.decode()
        for user in (self.duplicate_linked, self.duplicate_unlinked):
            self.assertNotIn(user.email, content)
            # ローカルパートだけでも識別子として漏れないこと
            self.assertNotIn(user.email.split('@')[0], content)

    def test_query_count_does_not_grow_with_member_count(self):
        """メンバーが増えてもクエリ数が増えない（Discord 連携判定の N+1 防止）"""
        self.client.force_login(self.owner)
        # 初回リクエストはセッション初期化などの追加クエリを含むため、計測前に温める
        self.client.get(self.url)

        with CaptureQueriesContext(connection) as before:
            self.client.get(self.url)

        for index in range(5):
            extra = make_discord_linked_user(
                user_name=DUPLICATE_NAME,
                email=f'extra{index}@example.com',
                discord_uid=f'discord-extra-{index}',
            )
            make_community_member(
                self.community, extra, role=CommunityMember.Role.STAFF
            )

        with CaptureQueriesContext(connection) as after:
            self.client.get(self.url)

        self.assertEqual(len(after.captured_queries), len(before.captured_queries))
