"""イベント詳細ページの X告知ボタンの表示権限のテスト.

ボタンの表示条件は告知画面（TweetEventWithTemplateView）の閲覧権限と同じく
「集会の主催者・スタッフ・superuser」に揃える。
"""
from django.test import Client, TestCase
from django.urls import reverse

from community.models import CommunityMember
from tests.factories import (
    make_community,
    make_community_member,
    make_event,
    make_event_detail,
    make_user,
)
from twitter.models import TwitterTemplate
from utils.vrchat_time import get_vrchat_today


class EventDetailTweetButtonVisibilityTests(TestCase):
    """X告知ボタンが誰に出て誰に出ないか."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user(user_name='tweet_owner', email='tweet_owner@example.com')
        cls.staff = make_user(user_name='tweet_staff', email='tweet_staff@example.com')
        cls.other_owner = make_user(user_name='tweet_other', email='tweet_other@example.com')
        cls.applicant = make_user(user_name='tweet_speaker', email='tweet_speaker@example.com')
        cls.superuser = make_user(
            user_name='tweet_admin',
            email='tweet_admin@example.com',
            is_superuser=True,
            is_staff=True,
        )
        cls.community = make_community(name='告知ボタン検証集会', owner=cls.owner)
        make_community_member(cls.community, cls.staff, role=CommunityMember.Role.STAFF)
        make_community(name='別の集会', owner=cls.other_owner)
        # 告知ボタンはイベント日から1週間以内だけ出るため、開催日を今日にする
        cls.event = make_event(cls.community, event_date=get_vrchat_today())
        # 集会メンバーでない発表者本人は編集ボタンは出るが、告知ボタンは出さない
        cls.event_detail = make_event_detail(cls.event, applicant=cls.applicant, status='approved')
        cls.template = TwitterTemplate.objects.create(
            name='告知テンプレA',
            community=cls.community,
            template='{event_name} {date}',
        )
        # テンプレート未登録の集会（「告知テンプレートを作成」の出し分け用）
        cls.bare_owner = make_user(user_name='tweet_bare', email='tweet_bare@example.com')
        bare_community = make_community(name='テンプレート未登録の集会', owner=cls.bare_owner)
        cls.bare_detail = make_event_detail(
            make_event(bare_community, event_date=get_vrchat_today()), status='approved',
        )
        cls.template_create_url = reverse('twitter:template_create')
        cls.tweet_url = reverse(
            'twitter:tweet_event_with_template',
            kwargs={'event_pk': cls.event.pk, 'template_pk': cls.template.pk},
        )

    def _get_html(self, user=None, detail=None) -> str:
        client = Client()
        if user is not None:
            client.force_login(user)
        detail = detail or self.event_detail
        response = client.get(reverse('event:detail', kwargs={'pk': detail.pk}))
        self.assertEqual(response.status_code, 200)
        return response.content.decode('utf-8')

    def test_owner_sees_tweet_button(self):
        self.assertIn(self.tweet_url, self._get_html(self.owner))

    def test_staff_sees_tweet_button(self):
        self.assertIn(self.tweet_url, self._get_html(self.staff))

    def test_superuser_without_membership_sees_tweet_button(self):
        """告知画面は superuser も通すので、ボタンも出す."""
        self.assertIn(self.tweet_url, self._get_html(self.superuser))

    def test_other_community_user_does_not_see_tweet_button(self):
        self.assertNotIn(self.tweet_url, self._get_html(self.other_owner))

    def test_anonymous_does_not_see_tweet_button(self):
        self.assertNotIn(self.tweet_url, self._get_html())

    def test_applicant_without_membership_does_not_see_tweet_button(self):
        """発表者本人は編集できても、告知画面は開けないのでボタンを出さない."""
        html = self._get_html(self.applicant)
        self.assertIn('id="admin-actions"', html)
        self.assertNotIn(self.tweet_url, html)

    def test_owner_sees_template_create_link_when_no_template(self):
        html = self._get_html(self.bare_owner, detail=self.bare_detail)
        self.assertIn(self.template_create_url, html)

    def test_superuser_without_membership_does_not_see_template_create_link(self):
        """テンプレート作成画面は集会メンバーだけが開けるので、superuser には出さない."""
        html = self._get_html(self.superuser, detail=self.bare_detail)
        self.assertNotIn(self.template_create_url, html)
