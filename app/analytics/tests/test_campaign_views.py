"""Campaign CRUD view の権限境界テスト。

他集会のキャンペーンを一覧で見られないこと、直接 URL でも 404 になることを担保する。
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from community.models import Community, CommunityMember

from analytics.models import Campaign

User = get_user_model()


class CampaignViewPermissionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user_a = User.objects.create_user(
            user_name='ownerA', email='a@example.com', password='pass-a',
        )
        cls.user_b = User.objects.create_user(
            user_name='ownerB', email='b@example.com', password='pass-b',
        )
        cls.community_a = Community.objects.create(
            name='集会A', frequency='毎週', organizers='主催A',
        )
        cls.community_b = Community.objects.create(
            name='集会B', frequency='毎週', organizers='主催B',
        )
        CommunityMember.objects.create(
            community=cls.community_a, user=cls.user_a,
            role=CommunityMember.Role.OWNER,
        )
        CommunityMember.objects.create(
            community=cls.community_b, user=cls.user_b,
            role=CommunityMember.Role.OWNER,
        )
        cls.campaign_a = Campaign.objects.create(
            community=cls.community_a, name='A の チラシ',
            utm_source='flyer', utm_medium='qr', utm_campaign='campaign-a',
        )
        cls.campaign_b = Campaign.objects.create(
            community=cls.community_b, name='B の チラシ',
            utm_source='flyer', utm_medium='qr', utm_campaign='campaign-b',
        )

    def test_list_only_shows_own_campaigns(self):
        self.client.force_login(self.user_a)
        res = self.client.get(reverse('analytics:campaign_list'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'A の チラシ')
        self.assertNotContains(res, 'B の チラシ')

    def test_update_other_communitys_campaign_returns_404(self):
        self.client.force_login(self.user_a)
        res = self.client.get(
            reverse('analytics:campaign_update', args=[self.campaign_b.pk])
        )
        self.assertEqual(res.status_code, 404)

    def test_delete_other_communitys_campaign_returns_404(self):
        self.client.force_login(self.user_a)
        res = self.client.post(
            reverse('analytics:campaign_delete', args=[self.campaign_b.pk])
        )
        self.assertEqual(res.status_code, 404)
        # B のキャンペーンは消えていない
        self.assertTrue(Campaign.objects.filter(pk=self.campaign_b.pk).exists())

    def test_create_form_limits_community_choices_to_accessible(self):
        self.client.force_login(self.user_a)
        res = self.client.get(reverse('analytics:campaign_create'))
        self.assertEqual(res.status_code, 200)
        # 集会A は選べる、B は選べない
        self.assertContains(res, '集会A')
        self.assertNotContains(res, '集会B')

    def test_create_rejects_inaccessible_community_via_form_post(self):
        """HTML 改ざんで他集会の community_id を POST しても保存できない（IDOR 防御）。"""
        self.client.force_login(self.user_a)
        res = self.client.post(
            reverse('analytics:campaign_create'),
            data={
                'community': self.community_b.pk,
                'name': 'IDOR テスト',
                'utm_source': 'flyer',
                'utm_medium': 'qr',
                'utm_campaign': 'idor-test',
                'landing_path': '/',
            },
        )
        # ModelChoiceField の queryset 制約で community が選択肢外 → 200 (form_invalid 再表示)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(
            Campaign.objects.filter(utm_campaign='idor-test').exists()
        )

    def test_create_generates_qr_image(self):
        self.client.force_login(self.user_a)
        res = self.client.post(
            reverse('analytics:campaign_create'),
            data={
                'community': self.community_a.pk,
                'name': 'QR生成テスト',
                'utm_source': 'flyer',
                'utm_medium': 'qr',
                'utm_campaign': 'qr-gen-test',
                'landing_path': '/',
            },
        )
        self.assertEqual(res.status_code, 302)
        created = Campaign.objects.get(utm_campaign='qr-gen-test')
        self.assertTrue(created.qr_image.name.startswith('qr_codes/'))
        self.assertTrue(created.qr_image.name.endswith('.png'))

    def test_landing_path_rejects_absolute_url(self):
        """landing_path に外部URLを入れたら保存できない。"""
        self.client.force_login(self.user_a)
        res = self.client.post(
            reverse('analytics:campaign_create'),
            data={
                'community': self.community_a.pk,
                'name': '外部URL',
                'utm_source': 'flyer',
                'utm_medium': 'qr',
                'utm_campaign': 'external-url',
                'landing_path': 'https://evil.example/',
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertFalse(
            Campaign.objects.filter(utm_campaign='external-url').exists()
        )

    def test_landing_path_rejects_schemeless_relative_url(self):
        """Open Redirect 変則パターン (//, /\\, /%2F, /%5C) が拒否される。"""
        bad_paths = ['//evil.example/', r'/\evil.example/', '/%2Fevil.example/', '/%5Cevil.example/']
        self.client.force_login(self.user_a)
        for i, bad in enumerate(bad_paths):
            with self.subTest(landing_path=bad):
                res = self.client.post(
                    reverse('analytics:campaign_create'),
                    data={
                        'community': self.community_a.pk,
                        'name': f'open-redirect-{i}',
                        'utm_source': 'flyer',
                        'utm_medium': 'qr',
                        'utm_campaign': f'open-redirect-{i}',
                        'landing_path': bad,
                    },
                )
                self.assertEqual(res.status_code, 200)
                self.assertFalse(
                    Campaign.objects.filter(utm_campaign=f'open-redirect-{i}').exists()
                )

    def test_utm_source_rejects_invalid_chars(self):
        """utm_source に改行・絵文字・スペース等を入れると拒否される。"""
        self.client.force_login(self.user_a)
        for i, bad in enumerate(['fly er', 'flyer\n', 'flyer　', 'flyer😀']):
            with self.subTest(utm_source=bad):
                res = self.client.post(
                    reverse('analytics:campaign_create'),
                    data={
                        'community': self.community_a.pk,
                        'name': f'bad-utm-{i}',
                        'utm_source': bad,
                        'utm_medium': 'qr',
                        'utm_campaign': f'bad-utm-{i}',
                        'landing_path': '/',
                    },
                )
                self.assertEqual(res.status_code, 200)
                self.assertFalse(
                    Campaign.objects.filter(utm_campaign=f'bad-utm-{i}').exists()
                )

    def test_form_without_user_has_empty_community_choices(self):
        """CampaignForm を user=None で誤って呼んでも community 選択肢が空になる（Fail Safe）。"""
        from analytics.forms import CampaignForm
        form = CampaignForm()
        self.assertEqual(form.fields['community'].queryset.count(), 0)

    def test_anonymous_user_redirected_from_list(self):
        res = self.client.get(reverse('analytics:campaign_list'))
        # LoginRequiredMixin はログインページへリダイレクト
        self.assertEqual(res.status_code, 302)
