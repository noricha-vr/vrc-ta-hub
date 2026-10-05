from datetime import date, time
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse

from community.models import Community
from event.models import Event, EventDetail
from event_calendar.calendar_utils import generate_google_calendar_url
from ta_hub.index_cache import build_index_database_context, get_index_view_cache_key


class SpecialEventCalendarTest(TestCase):
    def setUp(self):
        cache.clear()
        generate_google_calendar_url.cache_clear()
        self.addCleanup(cache.clear)
        self.addCleanup(generate_google_calendar_url.cache_clear)
        self.request = RequestFactory().get('/')
        self.request.user = AnonymousUser()
        self.day = date(2026, 10, 24)
        community = Community.objects.create(
            name='特別企画の集会', status='approved',
            poster_image='community/poster.png',
        )
        self.event = Event.objects.create(
            community=community, date=self.day, start_time=time(23, 30),
            duration=90, weekday='Sat',
        )
        self.special = EventDetail.objects.create(
            event=self.event, detail_type='SPECIAL', status='approved',
            theme='日付をまたぐ特別企画',
        )

    def test_calendar_link_and_article_link_work_in_both_layouts(self):
        context = build_index_database_context(
            self.request, self.day, get_index_view_cache_key(self.day),
        )
        for show_vket_notice in (False, True):
            with self.subTest(show_vket_notice=show_vket_notice):
                html = render_to_string('ta_hub/index.html', {
                    **context, 'show_vket_notice': show_vket_notice,
                }, request=self.request)
                soup = BeautifulSoup(html, 'html.parser')
                badge = soup.find('span', string='特別企画')
                link = badge.find_next_sibling('a')
                self.assertIsNotNone(link)
                params = parse_qs(urlparse(link['href']).query)
                self.assertEqual(params['text'], ['特別企画の集会'])
                self.assertEqual(params['dates'], ['20261024T233000/20261025T010000'])
                self.assertEqual(params['ctz'], ['Asia/Tokyo'])
                self.assertEqual(link['target'], '_blank')
                self.assertIsNone(link.find_parent('a'))
                card = badge.find_parent('div', class_='card')
                self.assertIsNotNone(card.find('a', href=reverse(
                    'event:detail', args=[self.special.pk],
                )))
                self.assertEqual(len(card.select('a[href*="google.com/calendar/render"]')), 1)

    def test_old_shared_cache_does_not_hide_calendar_url(self):
        cache.set(f'index_view_data_{self.day}', {
            'upcoming_events': [], 'upcoming_event_details': [],
            'special_events': [{'event': {'id': self.event.id}}],
        })
        context = build_index_database_context(
            self.request, self.day, get_index_view_cache_key(self.day),
        )
        self.assertIn('google_calendar_url', context['special_events'][0]['event'])
        with self.assertNumQueries(0):
            cached_context = build_index_database_context(
                self.request, self.day, get_index_view_cache_key(self.day),
            )
        self.assertEqual(context, cached_context)
