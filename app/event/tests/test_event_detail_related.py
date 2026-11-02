"""発表記事の関連 3 枠・キャッシュ・資料・集会カードの回帰テスト。"""
from datetime import date, time, timedelta
from unittest.mock import patch

from bs4 import BeautifulSoup
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from event.models import (
    EventDetail, related_event_details_cache_key, recent_event_details_cache_key,
    speaker_event_details_cache_key,
)
from event.views.detail import EventDetailView
from tests.factories import make_community, make_event, make_event_detail


class EventDetailRelatedTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.community = make_community(name='同じ集会', poster_image='poster/test.png')
        self.event = make_event(self.community, event_date=date(2026, 1, 1))
        self.other_event = make_event(make_community(name='別の集会'))
        self.current = self.article(self.event, speaker='発表者', h1='表示中の記事')
        self.view = EventDetailView()

    def article(self, event=None, **kwargs):
        defaults = dict(status='approved', h1='記事タイトル', contents='記事本文', speaker='別の発表者')
        defaults.update(kwargs)
        return make_event_detail(event or self.event, **defaults)

    def page(self, detail=None):
        response = self.client.get(reverse('event:detail', args=[(detail or self.current).pk]))
        self.assertEqual(response.status_code, 200)
        return response

    @staticmethod
    def ids(items):
        return [item['id'] for item in items]

    def test_speaker_excludes_self_ineligible_and_same_community_slot(self):
        same = self.article(speaker='発表者')
        eligible = self.article(self.other_event, speaker='発表者', h1='他集会での発表')
        for fields in (
            {'article_consent': EventDetail.ArticleConsent.NG}, {'status': 'pending'},
            {'status': 'rejected'}, {'deleted_at': timezone.now()}, {'h1': ''}, {'speaker': ''},
        ):
            self.article(self.other_event, **dict({'speaker': '発表者'}, **fields))
        response = self.page()
        self.assertEqual(self.ids(response.context['speaker_event_details']), [eligible.pk])
        self.assertEqual(self.ids(response.context['related_event_details']), [same.pk])
        self.assertContains(response, '発表者さんの他の発表')
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertIsNone(soup.select_one('[aria-labelledby="speaker-related-title"] img'))

    def test_speaker_limit_and_newest_order(self):
        articles = [self.article(self.other_event, speaker='発表者') for _ in range(5)]
        self.assertEqual(self.ids(self.page().context['speaker_event_details']),
                         [item.pk for item in reversed(articles[-3:])])

    def test_empty_speaker_or_no_articles_has_no_heading(self):
        for speaker in ('', '発表者'):
            with self.subTest(speaker=speaker):
                self.current.speaker = speaker
                self.current.save(update_fields=['speaker'])
                cache.clear()
                response = self.page()
                self.assertEqual(response.context['speaker_event_details'], [])
                self.assertNotContains(response, 'id="speaker-related-title"')
        self.current.speaker = ''
        with patch.object(self.view, '_cached_articles') as fetch:
            self.assertEqual(self.view._fetch_speaker_event_details(self.current, []), [])
            fetch.assert_not_called()

    def test_six_same_community_articles_skip_recent_lookup(self):
        for _ in range(6):
            self.article()
        self.article(self.other_event)
        with patch.object(EventDetailView, '_fetch_other_community_event_details') as fetch:
            response = self.page()
        fetch.assert_not_called()
        self.assertEqual(len(response.context['related_event_details']), 6)
        self.assertEqual(response.context['other_community_event_details'], [])
        self.assertIsNone(cache.get(recent_event_details_cache_key()))
        self.assertNotContains(response, 'id="other-related-title"')

    def test_two_same_community_articles_get_four_distinct_other_articles(self):
        same = [self.article() for _ in range(2)]
        speaker_articles = [self.article(self.other_event, speaker='発表者') for _ in range(3)]
        others = [self.article(self.other_event) for _ in range(5)]
        # 不適格な新着が eligible な記事を押し出さないことも確かめる。
        for fields in ({'article_consent': EventDetail.ArticleConsent.NG}, {'status': 'pending'},
                       {'h1': ''}, {'deleted_at': timezone.now()}):
            self.article(self.other_event, **fields)
        response = self.page()
        related = response.context['related_event_details']
        speaker = response.context['speaker_event_details']
        recent = response.context['other_community_event_details']
        self.assertEqual(set(self.ids(related)), {item.pk for item in same})
        self.assertEqual(set(self.ids(speaker)), {item.pk for item in speaker_articles})
        self.assertEqual(self.ids(recent), [item.pk for item in reversed(others[-4:])])
        self.assertTrue(all(item['event__community_id'] != self.community.pk for item in recent))
        soup = BeautifulSoup(response.content, 'html.parser')
        for slot, items in (('same_community', related), ('same_speaker', speaker),
                            ('other_communities', recent)):
            links = soup.select(f'a[data-related-slot="{slot}"]')
            self.assertEqual([int(a['data-to-detail-id']) for a in links], self.ids(items))
            self.assertEqual([a['data-related-position'] for a in links],
                             [str(i) for i in range(1, len(items) + 1)])
        # GA4 にカスタムディメンションを登録せずに枠ごとに数えるため、枠の名前はイベント名に入れる
        html = response.content.decode('utf-8')
        self.assertIn("gtag('event', 'related_click_' + link.dataset.relatedSlot", html)
        self.assertIn("gtag('event', 'related_impression_' + slot[0]", html)
        for slot, items in (('same_community', related), ('same_speaker', speaker),
                            ('other_communities', recent)):
            self.assertIn(f"['{slot}', {len(items)}]", html)

    def test_community_slot_excludes_all_ineligible_articles(self):
        good = self.article()
        for fields in ({'article_consent': EventDetail.ArticleConsent.NG}, {'status': 'pending'}, {'status': 'rejected'},
                       {'h1': ''}, {'deleted_at': timezone.now()}):
            self.article(**fields)
        self.assertEqual(self.ids(self.page().context['related_event_details']), [good.pk])

    def test_cache_contains_raw_data_and_is_shared_across_pages(self):
        detail = self.article(thumbnail_image='thumbnail/test.png', youtube_url='https://youtu.be/abcdefghijk')
        first = self.page()
        key = related_event_details_cache_key(self.community.pk)
        rows = cache.get(key)
        self.assertIn(self.current.pk, self.ids(rows))
        self.assertIn(detail.pk, self.ids(rows))
        row = next(item for item in rows if item['id'] == detail.pk)
        self.assertEqual(row['thumbnail_image'], 'thumbnail/test.png')
        self.assertNotIn('thumbnail_url', row)
        self.assertNotIn('video_id', row)
        card = first.context['related_event_details'][0]
        self.assertTrue(card['thumbnail_url'])
        self.assertEqual(card['video_id'], 'abcdefghijk')
        second = self.page(detail)
        self.assertEqual(self.ids(second.context['related_event_details']), [self.current.pk])
        self.assertIn('v2', key)

    def test_cached_urls_are_resolved_again_after_read(self):
        self.article(thumbnail_image='thumbnail/test.png')
        self.view._fetch_related_event_details(self.current)
        storage = EventDetail._meta.get_field('thumbnail_image').storage
        with patch.object(storage, 'url', return_value='https://example.test/fresh.png') as url:
            # 一覧は作り直さず、公開条件の確かめ直しの 1 本だけを引く
            with self.assertNumQueries(1):
                cards = self.view._fetch_related_event_details(self.current)
        url.assert_called_once_with('thumbnail/test.png')
        self.assertEqual(cards[0]['thumbnail_url'], 'https://example.test/fresh.png')

    def test_speaker_and_recent_caches_reused_with_only_publication_recheck(self):
        """キャッシュが効いている時は一覧を作り直さず、公開条件の確かめ直し（枠ごとに 1 本）だけを引く。"""
        self.article(self.other_event, speaker='発表者')
        self.view._fetch_speaker_event_details(self.current, [])
        self.view._fetch_other_community_event_details(self.current, [], [])
        with self.assertNumQueries(2):
            self.view._fetch_speaker_event_details(self.current, [])
            self.view._fetch_other_community_event_details(self.current, [], [])

    def test_cached_rows_withdrawn_by_bulk_update_are_not_shown(self):
        """シグナルを通らない一括更新で取り下げた記事も、キャッシュの寿命を待たずに 3 つの枠から消える。"""
        same = self.article(h1='同じ集会の記事')
        by_speaker = self.article(self.other_event, speaker='発表者', h1='同じ発表者の記事')
        recent = self.article(self.other_event, h1='別の集会の新着')
        before = self.page().context
        self.assertIn(same.pk, self.ids(before['related_event_details']))
        self.assertIn(by_speaker.pk, self.ids(before['speaker_event_details']))
        self.assertIn(recent.pk, self.ids(before['other_community_event_details']))

        EventDetail.objects.filter(pk=same.pk).update(article_consent=EventDetail.ArticleConsent.NG)
        EventDetail.objects.filter(pk=by_speaker.pk).update(status='rejected')
        EventDetail.objects.filter(pk=recent.pk).update(deleted_at=timezone.now())

        after = self.page().context
        self.assertNotIn(same.pk, self.ids(after['related_event_details']))
        self.assertNotIn(by_speaker.pk, self.ids(after['speaker_event_details']))
        self.assertNotIn(recent.pk, self.ids(after['other_community_event_details']))

    def test_pdf_thumbnail_written_by_update_clears_card_caches(self):
        """PDF から作ったサムネイルは UPDATE で書くのでシグナルを通らない。書いた後に関連のキャッシュを消す。"""
        from event.services.media_service import _store_thumbnail_if_empty

        detail = self.article(slide_file='slide/source.pdf', speaker='発表者')
        keys = [related_event_details_cache_key(self.community.pk), recent_event_details_cache_key(),
                speaker_event_details_cache_key('発表者')]
        for key in keys:
            cache.set(key, ['cached'])
        detail.thumbnail_image.name = 'thumbnail/generated.jpg'
        with self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(_store_thumbnail_if_empty(detail, 'slide/source.pdf'))
        for key in keys:
            self.assertIsNone(cache.get(key))

    def test_all_caches_invalidated_on_title_consent_and_card_changes(self):
        detail = self.article(speaker='旧発表者')
        keys = [related_event_details_cache_key(self.community.pk), recent_event_details_cache_key(),
                speaker_event_details_cache_key('旧発表者'), speaker_event_details_cache_key('新発表者')]
        for field, value in (
            ('h1', '更新した記事'), ('article_consent', EventDetail.ArticleConsent.NG), ('speaker', '新発表者'),
            ('theme', '更新したテーマ'), ('thumbnail_image', 'thumbnail/new.png'),
            ('youtube_url', 'https://youtu.be/abcdefghijk'), ('status', 'pending'),
            ('deleted_at', timezone.now()),
        ):
            with self.subTest(field=field):
                for key in keys:
                    cache.set(key, ['cached'])
                setattr(detail, field, value)
                with self.captureOnCommitCallbacks(execute=True):
                    detail.save(update_fields=[field])
                    self.assertIsNotNone(cache.get(keys[0]))
                expected = keys if field == 'speaker' else [keys[0], keys[1],
                    speaker_event_details_cache_key(detail.speaker)]
                for key in expected:
                    self.assertIsNone(cache.get(key))

    def test_speaker_key_is_fixed_length_and_contains_no_name(self):
        key = speaker_event_details_cache_key('発表者 / 長い名前' * 100)
        self.assertLess(len(key), 250)
        self.assertNotIn('発表者', key)

    def test_thumbnail_fallback_order_never_uses_community_poster(self):
        thumbnail = self.article(thumbnail_image='thumbnail/test.png', youtube_url='https://youtu.be/abcdefghijk')
        video = self.article(youtube_url='https://youtu.be/abcdefghijk')
        text = self.article(theme='画像なしのテーマ')
        soup = BeautifulSoup(self.page().content, 'html.parser')
        cards = {int(card.select_one('a')['data-to-detail-id']): card for card in soup.select('.ed-rel-card')}
        self.assertIn('thumbnail/test.png', cards[thumbnail.pk].img['src'])
        self.assertEqual(cards[video.pk].img['src'], 'https://img.youtube.com/vi/abcdefghijk/mqdefault.jpg')
        self.assertIsNone(cards[text.pk].img)
        self.assertEqual(cards[text.pk].select_one('.ed-titlecard-theme').text, '画像なしのテーマ')
        self.assertIn('発表タイトル', cards[text.pk].text)
        self.assertNotIn('poster/', str(cards))

    def test_ng_page_keeps_related_articles_but_hides_body(self):
        self.current.article_consent = EventDetail.ArticleConsent.NG
        self.current.save(update_fields=['article_consent'])
        related = self.article(h1='別の発表の記事')
        response = self.page()
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertIsNone(soup.select_one('.ed-body'))
        self.assertIsNone(soup.select_one('.ed-theme'))
        self.assertEqual(self.ids(response.context['related_event_details']), [related.pk])


class EventDetailLayoutTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.today = date(2026, 11, 2)
        self.community = make_community(name='開催案内の集会')
        self.event = make_event(self.community, event_date=self.today - timedelta(days=7))
        self.detail = make_event_detail(self.event, status='approved', h1='記事見出し',
                                        theme='発表テーマ', contents='本文')

    def page(self):
        with patch('event.views.detail.get_vrchat_today', return_value=self.today):
            response = self.client.get(reverse('event:detail', args=[self.detail.pk]))
        self.assertEqual(response.status_code, 200)
        return response, BeautifulSoup(response.content, 'html.parser')

    def test_actions_absent_without_slides_or_discord(self):
        _, soup = self.page()
        self.assertIsNone(soup.select_one('.ed-actions'))

    def test_slide_links_and_discord_video_are_external_buttons(self):
        self.detail.slide_url = 'https://example.test/slides'
        self.detail.slide_file = 'slide/test.pdf'
        self.detail.youtube_url = 'https://discord.com/channels/1/2/3'
        self.detail.save(update_fields=['slide_url', 'slide_file', 'youtube_url'])
        _, soup = self.page()
        actions = soup.select('.ed-actions a')
        self.assertEqual([a.get_text(strip=True) for a in actions],
                         ['発表スライドを開く', 'スライドの PDF を開く', 'Discord で動画を見る'])
        for action in actions:
            self.assertEqual(action['target'], '_blank')
            self.assertEqual(set(action['rel']), {'noopener', 'noreferrer'})
        self.assertEqual(actions[0]['href'], self.detail.slide_url)
        self.assertEqual(actions[1]['href'], self.detail.slide_file.url)
        self.assertEqual(actions[2]['href'], self.detail.youtube_url)
        self.assertIsNone(soup.select_one('.ed-media'))
        self.assertIsNone(soup.iframe)

    def test_next_event_uses_nearest_date_then_start_time(self):
        make_event(self.community, event_date=self.today + timedelta(days=7))
        make_event(self.community, event_date=self.today, start_time=time(23))
        nearest = make_event(self.community, event_date=self.today, start_time=time(21))
        response, soup = self.page()
        self.assertEqual(response.context['next_event'], nearest)
        self.assertEqual(soup.select_one('.ed-schedule dt').text, '次回')
        self.assertIn('2026年11月2日（月） 21:00〜22:00', soup.select_one('.ed-schedule dd').text)
        self.assertIsNotNone(soup.select_one('#community-card-title'))
        self.assertIsNone(soup.select_one('.ed-poster'))

    def test_community_card_without_upcoming_event_or_poster(self):
        _, soup = self.page()
        self.assertEqual([dt.text for dt in soup.select('.ed-schedule dt')], ['開催'])
        card = soup.select_one('[aria-labelledby="community-card-title"]')
        self.assertIsNotNone(card)
        self.assertEqual(card.a.text, '参加方法を見る')
        self.assertEqual(card.a['href'], reverse('community:detail', args=[self.community.pk]))
        self.assertIsNone(soup.select_one('#collapseExample'))

    def test_header_has_theme_speaker_link_date_and_single_main(self):
        _, soup = self.page()
        self.assertEqual(soup.select_one('.ed-header h1').text, '記事見出し')
        self.assertEqual(soup.select_one('.ed-theme').text, '発表「発表テーマ」')
        self.assertIn('?speaker=', soup.select_one('.ed-byline a')['href'])
        self.assertIn('22:00〜22:30', soup.select_one('.ed-byline time').text)
        self.assertEqual(soup.select_one('.ed-byline time')['datetime'], '2026-10-26T22:00')
        self.assertEqual(len(soup.select('main')), 1)
        self.assertEqual(soup.select_one('.ed-header').find_parent('article')['class'], ['col-lg-8'])
        self.assertIsNotNone(soup.select_one('aside.col-lg-4'))

    def test_special_header_has_only_date_and_start_time(self):
        self.detail.detail_type = 'SPECIAL'
        self.detail.save(update_fields=['detail_type'])
        _, soup = self.page()
        self.assertIsNone(soup.select_one('.ed-byline a'))
        self.assertNotIn('〜', soup.select_one('.ed-byline time').text)
        self.assertIn('22:00', soup.select_one('.ed-byline time').text)

    def test_empty_speaker_has_no_link_or_separator(self):
        self.detail.speaker = ''
        self.detail.save(update_fields=['speaker'])
        _, soup = self.page()
        self.assertIsNone(soup.select_one('.ed-byline a'))
        self.assertNotIn('·', soup.select_one('.ed-byline').text)

    def test_duplicate_theme_hidden(self):
        self.detail.h1 = self.detail.theme
        self.detail.save(update_fields=['h1'])
        _, soup = self.page()
        self.assertIsNone(soup.select_one('.ed-theme'))
