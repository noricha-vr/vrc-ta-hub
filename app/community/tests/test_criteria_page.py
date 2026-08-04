"""集会・協力団体の掲載審査基準ページのテスト."""

from bs4 import BeautifulSoup
from django.test import TestCase
from django.urls import reverse

from tests.factories import make_user


class CommunityCriteriaPageTest(TestCase):
    """公開審査基準と登録導線を検証する."""

    def setUp(self):
        self.criteria_url = reverse('community:criteria')

    def test_anonymous_user_can_view_criteria_with_required_structure(self):
        response = self.client.get(self.criteria_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'community/criteria.html')
        soup = BeautifulSoup(response.content, 'html.parser')
        for heading_id in ('category-definitions', 'examples', 'required-conditions', 'evaluation-method'):
            self.assertIsNotNone(soup.select_one(f'#{heading_id}'))

        required_conditions = soup.select_one('#required-conditions').find_parent('section')
        self.assertEqual(len(required_conditions.select('ol.list-group-numbered > li')), 5)

        evaluation_method = soup.select_one('#evaluation-method').find_parent('section')
        self.assertEqual(len(evaluation_method.select('tbody > tr')), 4)
        approval_line = evaluation_method.select_one('.alert[role="note"]')
        self.assertIn('合計6点以上', approval_line.get_text())

    def test_registration_page_links_to_criteria(self):
        user = make_user(
            user_name='審査基準テストユーザー',
            email='criteria@example.com',
            password='testpass123',
        )
        self.client.force_login(user)

        response = self.client.get(reverse('community:create'))

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, 'html.parser')
        criteria_alert = soup.select_one('div.alert.alert-info[role="note"]')
        criteria_link = criteria_alert.select_one(f'a.alert-link[href="{self.criteria_url}"]')
        self.assertEqual(criteria_link.get_text(strip=True), '掲載・審査基準')

    def test_common_footer_links_to_criteria(self):
        response = self.client.get(reverse('ta_hub:about'))

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, 'html.parser')
        footer_link = soup.select_one(f'footer a[href="{self.criteria_url}"]')
        self.assertEqual(footer_link.get_text(strip=True), '掲載・審査基準')
