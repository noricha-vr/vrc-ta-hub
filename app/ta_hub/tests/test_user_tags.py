"""user_tags テンプレートタグのテスト"""

from django.template import Context, Template
from django.test import RequestFactory, TestCase

from community.models import Community, CommunityMember
from user_account.models import CustomUser


class GetUserCommunityNameTagTest(TestCase):
    """get_user_community_name テンプレートタグのテスト"""

    def setUp(self):
        self.factory = RequestFactory()

        # 主催者ユーザーを作成
        self.owner_user = CustomUser.objects.create_user(
            email='owner@example.com',
            password='testpass123',
            user_name='主催者ユーザー'
        )

        # スタッフユーザーを作成
        self.staff_user = CustomUser.objects.create_user(
            email='staff@example.com',
            password='testpass123',
            user_name='スタッフユーザー'
        )

        # 集会未所持ユーザーを作成
        self.no_community_user = CustomUser.objects.create_user(
            email='nocomm@example.com',
            password='testpass123',
            user_name='集会なしユーザー'
        )

        # 後方互換テスト用ユーザー（custom_userで関連付け）
        self.legacy_user = CustomUser.objects.create_user(
            email='legacy@example.com',
            password='testpass123',
            user_name='レガシーユーザー'
        )

        # CommunityMember経由の集会を作成
        self.community = Community.objects.create(
            name='テスト技術集会ABC',
            frequency='毎週',
            organizers='テスト主催者'
        )

        # 主催者をCommunityMemberとして追加
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner_user,
            role=CommunityMember.Role.OWNER
        )

        # スタッフをCommunityMemberとして追加
        CommunityMember.objects.create(
            community=self.community,
            user=self.staff_user,
            role=CommunityMember.Role.STAFF
        )

        # 後方互換用の集会（custom_userで関連付け）
        self.legacy_community = Community.objects.create(
            name='レガシー集会123',
            frequency='月1回',
            organizers='レガシー主催者',
            custom_user=self.legacy_user
        )

    def _render_template(self, user):
        """テンプレートタグをレンダリングするヘルパー"""
        template = Template(
            '{% load user_tags %}'
            '{% get_user_community_name as name %}'
            '{{ name }}'
        )
        context = Context({'user': user})
        return template.render(context)

    def test_owner_user_sees_community_name(self):
        """主催者ユーザーは集会名が表示される"""
        result = self._render_template(self.owner_user)
        # 8文字で切り捨てなので「テスト技術集会A」（元: テスト技術集会ABC）
        self.assertEqual(result, 'テスト技術集会A')

    def test_staff_user_sees_community_name(self):
        """スタッフユーザーは集会名が表示される"""
        result = self._render_template(self.staff_user)
        # 8文字で切り捨てなので「テスト技術集会A」（元: テスト技術集会ABC）
        self.assertEqual(result, 'テスト技術集会A')

    def test_no_community_user_sees_empty(self):
        """集会未所持ユーザーには空文字が返される"""
        result = self._render_template(self.no_community_user)
        self.assertEqual(result, '')

    def test_legacy_user_sees_community_name(self):
        """後方互換（custom_user関連付け）ユーザーも集会名が表示される"""
        result = self._render_template(self.legacy_user)
        # 8文字で切り捨てなので「レガシー集会12」（元: レガシー集会123）
        self.assertEqual(result, 'レガシー集会12')

    def test_anonymous_user_sees_empty(self):
        """未ログインユーザーには空文字が返される"""
        from django.contrib.auth.models import AnonymousUser
        result = self._render_template(AnonymousUser())
        self.assertEqual(result, '')

    def test_community_member_takes_priority_over_legacy(self):
        """CommunityMemberがcustom_userより優先される"""
        # legacy_userをCommunityMemberとして別の集会に追加
        priority_community = Community.objects.create(
            name='優先集会テスト',
            frequency='毎週',
            organizers='優先主催者'
        )
        CommunityMember.objects.create(
            community=priority_community,
            user=self.legacy_user,
            role=CommunityMember.Role.STAFF
        )

        result = self._render_template(self.legacy_user)
        # CommunityMember経由の「優先集会テスト」が表示される
        self.assertEqual(result, '優先集会テスト')

    def test_community_name_truncated_to_8_chars(self):
        """集会名は8文字で切り捨てられる"""
        long_name_community = Community.objects.create(
            name='12345678901234567890',  # 20文字
            frequency='毎週',
            organizers='テスト'
        )
        long_name_user = CustomUser.objects.create_user(
            email='longname@example.com',
            password='testpass123',
            user_name='テストユーザー'
        )
        CommunityMember.objects.create(
            community=long_name_community,
            user=long_name_user,
            role=CommunityMember.Role.STAFF
        )

        result = self._render_template(long_name_user)
        self.assertEqual(result, '12345678')
        self.assertEqual(len(result), 8)
