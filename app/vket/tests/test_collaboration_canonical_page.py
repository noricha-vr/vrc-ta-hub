"""コラボの公開日程・発表一覧とアーカイブへの導線。"""
from datetime import timedelta
from unittest.mock import patch

from bs4 import BeautifulSoup
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from event.models import EventDetail
from news.models import Category, Post
from tests.factories import make_community, make_discord_linked_user, make_event, make_event_detail
from vket.models import VketCollaboration, VketParticipation


class CollaborationCanonicalPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.collaboration = VketCollaboration.objects.create(
            slug='canonical-collaboration', name='公開日程コラボ',
            period_start=today, period_end=today + timedelta(days=7),
            registration_deadline=today, lt_deadline=today + timedelta(days=2),
            phase=VketCollaboration.Phase.ENTRY_OPEN,
            hashtags=['#公開日程'],
        )
        cls.category = Category.objects.create(name='アーカイブ', slug='canonical-archive')
        cls.post = Post.objects.create(
            title='発表動画まとめ', slug='canonical-archive', category=cls.category,
            body_markdown='アーカイブ本文', is_published=True,
        )

    def setUp(self):
        cache.clear()
        self.url = reverse('vket:detail', args=[self.collaboration.pk])

    def _participation(self, index=0, status='approved', owner=None):
        community = make_community(name=f'公開集会{index}', status=status, owner=owner)
        event = make_event(
            community, event_date=self.collaboration.period_start,
            start_time='21:00', duration=90,
        )
        VketParticipation.objects.create(
            collaboration=self.collaboration, community=community, published_event=event,
        )
        return event

    def _detail(self, event, index=0, status='approved'):
        return make_event_detail(
            event, status=status,
            speaker=f'発表者{index}', theme=f'発表テーマ{index}', start_time='21:15',
            slide_url=f'https://example.com/slides/{index}',
            youtube_url=f'https://www.youtube.com/watch?v=video{index}',
        )

    def _set_archive(self, value):
        self.collaboration.settings_json = {'archive_news_slug': value}
        self.collaboration.save(update_fields=['settings_json'])

    def test_timetable_and_google_calendar_button(self):
        event = self._participation()
        self._detail(event)
        response = self.client.get(self.url)
        soup = BeautifulSoup(response.content, 'html.parser')
        block = soup.select_one('#vket-timetable .vket-timetable-block-body')
        self.assertEqual(block['href'], reverse('community:detail', args=[event.community_id]))
        calendar = soup.select_one('#vket-timetable .vket-timetable-calendar')
        self.assertIn('www.google.com/calendar/render?', calendar['href'])
        self.assertEqual(len(soup.select('.vket-week-panel')), 2)
        self.assertEqual(len(soup.select('[data-vket-week]')), 2)
        self.assertContains(response, "tab.classList.toggle('btn-primary', active)")

    def test_daily_list_shows_all_approved_presentations_and_materials(self):
        event = self._participation()
        details = [self._detail(event, i) for i in range(2)]
        self._detail(event, 99, status='pending')
        response = self.client.get(self.url)
        entries = response.context['grouped_entries'][0]['entries']
        self.assertEqual([d.pk for d in entries[0]['lt_details']], [d.pk for d in details])
        for detail in details:
            self.assertContains(response, f'発表: {detail.speaker}「{detail.theme}」')
            self.assertContains(response, f'href="{detail.slide_url}"')
            self.assertContains(response, f'href="{detail.youtube_url}"')
        self.assertNotContains(response, '発表テーマ99')

    def test_uploaded_slide_pdf_is_linked_without_extra_queries(self):
        event = self._participation()
        detail = self._detail(event)
        EventDetail.objects.filter(pk=detail.pk).update(slide_url='', slide_file='slide/uploaded.pdf')
        # フレームワーク側の初回キャッシュを温めてから、ページ全体を計測する。
        self.client.get(self.url)
        with self.assertNumQueries(5):
            response = self.client.get(self.url)
        soup = BeautifulSoup(response.content, 'html.parser')
        link = soup.find('a', string='スライド')
        self.assertIsNotNone(link)
        self.assertTrue(link['href'].endswith('slide/uploaded.pdf'))

    def test_unapproved_community_is_hidden_from_both_lists(self):
        for index, status in enumerate(('pending', 'rejected')):
            self._detail(self._participation(index, status=status), index)
        response = self.client.get(self.url)
        self.assertEqual(response.context['grouped_entries'], [])
        self.assertEqual(response.context['vket_timetable']['scheduled_count'], 0)
        self.assertNotContains(response, '公開集会')
        self.assertNotContains(response, '発表テーマ')

    def test_anonymous_viewer_sees_period_and_hashtags_without_deadlines(self):
        response = self.client.get(self.url)
        self.assertContains(response, '期間:')
        self.assertContains(response, '#公開日程')
        self.assertNotContains(response, '締切')

    def test_authenticated_viewer_without_membership_cannot_see_deadlines(self):
        user = make_discord_linked_user()
        self.client.force_login(user)
        self.assertNotContains(self.client.get(self.url), '締切')

    def test_applicant_can_see_named_deadlines(self):
        user = make_discord_linked_user()
        self._participation(owner=user)
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertTrue(response.context['can_apply'])
        self.assertContains(response, '参加登録の締切:')
        self.assertContains(response, '発表登録の締切:')
        self.assertNotContains(response, '締切1')
        self.assertNotContains(response, '締切2')

    def test_admin_without_membership_can_see_named_deadlines(self):
        user = make_discord_linked_user(is_staff=True)
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertTrue(response.context['is_admin'])
        self.assertFalse(response.context['can_apply'])
        self.assertContains(response, '参加登録の締切:')
        self.assertContains(response, '発表登録の締切:')

    def test_published_archive_is_prominent_after_period(self):
        self._set_archive(self.post.slug)
        with patch('vket.views.public.get_vrchat_today', return_value=self.collaboration.period_end + timedelta(days=1)):
            response = self.client.get(self.url)
        self.assertContains(response, '発表動画のアーカイブ</h2>')
        self.assertContains(response, f'href="{reverse("news:detail", args=[self.post.slug])}"')
        self.assertContains(response, 'alert alert-primary p-4')

    def test_archive_is_subtle_before_and_during_period(self):
        self._set_archive(self.post.slug)
        for today in (self.collaboration.period_start - timedelta(days=1), self.collaboration.period_end):
            with self.subTest(today=today), patch('vket.views.public.get_vrchat_today', return_value=today):
                response = self.client.get(self.url)
                self.assertContains(response, f'href="{reverse("news:detail", args=[self.post.slug])}"')
                self.assertNotContains(response, '発表動画のアーカイブ</h2>')

    def test_unpublished_archive_is_hidden_even_from_staff(self):
        self._set_archive(self.post.slug)
        self.post.is_published = False
        self.post.save(update_fields=['is_published'])
        self.client.force_login(make_discord_linked_user(is_staff=True))
        with patch('vket.views.public.get_vrchat_today', return_value=self.collaboration.period_end + timedelta(days=1)):
            response = self.client.get(self.url)
        self.assertNotContains(response, '発表動画のアーカイブ')
        self.assertIsNone(response.context['archive_news'])

    def test_missing_or_non_string_archive_slug_is_ignored(self):
        for slug in ('missing-archive', '', None, 42, True, ['archive'], {'slug': self.post.slug}):
            with self.subTest(slug=slug):
                self._set_archive(slug)
                with patch('vket.views.public.get_vrchat_today', return_value=self.collaboration.period_end + timedelta(days=1)):
                    response = self.client.get(self.url)
                self.assertNotContains(response, '発表動画のアーカイブ')
                self.assertIsNone(response.context['archive_news'])

    def test_non_object_settings_are_ignored(self):
        for settings in (None, [], 'invalid', 5):
            with self.subTest(settings=settings):
                self.collaboration.settings_json = settings
                self.collaboration.save(update_fields=['settings_json'])
                self.assertNotContains(self.client.get(self.url), '発表動画のアーカイブ')

    def test_query_count_stays_constant_when_participations_increase(self):
        self._set_archive(self.post.slug)
        event = self._participation()
        for index in range(2):
            self._detail(event, index)
        # フレームワーク側の初回キャッシュを温めてから、ページ全体を計測する。
        self.client.get(self.url)
        with self.assertNumQueries(6):
            response = self.client.get(self.url)
        self.assertEqual(response.context['scheduled_count'], 1)
        for index in range(1, 10):
            event = self._participation(index)
            self._detail(event, index * 2)
            self._detail(event, index * 2 + 1)
        with self.assertNumQueries(6):
            response = self.client.get(self.url)
        self.assertEqual(response.context['scheduled_count'], 10)
        self.assertContains(response, 'https://example.com/slides/19')

    def test_footer_links_to_collaboration_list(self):
        response = self.client.get(self.url)
        soup = BeautifulSoup(response.content, 'html.parser')
        link = soup.select_one(f'footer a[href="{reverse("vket:list")}"]')
        self.assertIsNotNone(link)
        self.assertEqual(link.get_text(strip=True), 'Vket コラボ')
