"""発表一覧ページ（旧「発表履歴」）の表示・絞り込みを検証する。"""

import json
from datetime import date

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from event.models import EventDetail
from tests.factories import make_community, make_event, make_event_detail


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class PresentationListViewTests(TestCase):
    """既定表示・表示切替・絞り込みの挙動。"""

    def setUp(self):
        cache.clear()
        self.url = reverse('event:detail_history')

        self.community = make_community(name='個人開発集会')
        self.other_community = make_community(name='研究発表集会')
        self.event = make_event(self.community, event_date=date(2026, 5, 10))
        self.other_event = make_event(self.other_community, event_date=date(2026, 5, 11))

        self.with_contents = make_event_detail(
            self.event,
            status='approved',
            speaker='のり',
            theme='Djangoの話',
            contents='# 見出し\n\nDjango で作った話をします。',
        )
        self.with_video = make_event_detail(
            self.other_event,
            status='approved',
            speaker='たろう',
            theme='Rubyの話',
            youtube_url='https://www.youtube.com/watch?v=abcdefghijk',
        )
        self.without_materials = make_event_detail(
            self.event,
            status='approved',
            speaker='はなこ',
            theme='資料なしの発表',
        )

    def _ids(self, response):
        return [detail.id for detail in response.context['event_details']]

    def test_default_view_shows_only_details_with_materials(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        detail_ids = self._ids(response)
        self.assertIn(self.with_contents.id, detail_ids)
        self.assertIn(self.with_video.id, detail_ids)
        self.assertNotIn(self.without_materials.id, detail_ids)

    def test_view_all_shows_every_approved_detail(self):
        response = self.client.get(self.url, {'view': 'all'})

        detail_ids = self._ids(response)
        self.assertIn(self.without_materials.id, detail_ids)
        self.assertEqual(len(detail_ids), 3)

    def test_counts_reflect_current_filters(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['materials_count'], 2)
        self.assertEqual(response.context['all_count'], 3)

        filtered = self.client.get(self.url, {'community_name': '個人開発集会'})
        self.assertEqual(filtered.context['materials_count'], 1)
        self.assertEqual(filtered.context['all_count'], 2)

    def test_invalid_view_and_type_values_are_ignored(self):
        response = self.client.get(self.url, {'view': 'everything', 'type': 'lt'})

        self.assertFalse(response.context['show_all'])
        self.assertFalse(response.context['is_special'])
        self.assertNotIn('view=', response.context['current_query_params'])
        self.assertNotIn('type=', response.context['current_query_params'])


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class PresentationListSearchTests(TestCase):
    """キーワード検索と既存パラメータの後方互換。"""

    def setUp(self):
        cache.clear()
        self.url = reverse('event:detail_history')

        self.community = make_community(name='キーワード集会')
        self.event = make_event(self.community, event_date=date(2026, 5, 10))

        self.by_theme = make_event_detail(
            self.event, status='approved', speaker='話者A', theme='Rustの所有権',
            contents='本文あり',
        )
        self.by_h1 = make_event_detail(
            self.event, status='approved', speaker='話者B', theme='別テーマ',
            h1='Elixirで作る並行処理', contents='本文あり',
        )
        self.by_speaker = make_event_detail(
            self.event, status='approved', speaker='ゼータ', theme='別テーマ2',
            contents='本文あり',
        )

    def _ids(self, response):
        return [detail.id for detail in response.context['event_details']]

    def test_q_matches_theme(self):
        response = self.client.get(self.url, {'q': 'Rust'})
        self.assertEqual(self._ids(response), [self.by_theme.id])

    def test_q_matches_h1(self):
        response = self.client.get(self.url, {'q': 'Elixir'})
        self.assertEqual(self._ids(response), [self.by_h1.id])

    def test_q_matches_speaker(self):
        response = self.client.get(self.url, {'q': 'ゼータ'})
        self.assertEqual(self._ids(response), [self.by_speaker.id])

    def test_q_matches_community_name(self):
        response = self.client.get(self.url, {'q': 'キーワード集会'})
        self.assertEqual(len(self._ids(response)), 3)

    def test_theme_param_is_treated_as_keyword(self):
        """旧 theme パラメータは q と同じ横断検索として扱う"""
        response = self.client.get(self.url, {'theme': 'Elixir'})
        self.assertEqual(self._ids(response), [self.by_h1.id])
        self.assertEqual(response.context['keyword'], 'Elixir')

    def test_speaker_param_filters_alone(self):
        response = self.client.get(self.url, {'speaker': '話者A'})
        self.assertEqual(self._ids(response), [self.by_theme.id])

    def test_community_name_param_filters_alone(self):
        response = self.client.get(self.url, {'community_name': 'キーワード集会'})
        self.assertEqual(len(self._ids(response)), 3)

    def test_empty_result_shows_guidance(self):
        response = self.client.get(self.url, {'q': '該当なしのキーワード'})
        self.assertEqual(self._ids(response), [])
        self.assertContains(response, '該当する発表はありません')


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class PresentationListTypeTests(TestCase):
    """種別（発表 / 特別企画・ブログ）の切り替え。"""

    def setUp(self):
        cache.clear()
        self.url = reverse('event:detail_history')

        self.community = make_community(name='種別テスト集会')
        self.event = make_event(self.community, event_date=date(2026, 5, 10))

        self.lt = make_event_detail(
            self.event, status='approved', theme='通常の発表', contents='本文',
        )
        self.special = make_event_detail(
            self.event, status='approved', detail_type='SPECIAL',
            theme='特別企画です', contents='本文',
        )
        self.blog = make_event_detail(
            self.event, status='approved', detail_type='BLOG',
            theme='ブログ記事です', contents='本文',
        )

    def _ids(self, response):
        return [detail.id for detail in response.context['event_details']]

    def test_default_excludes_special_and_blog(self):
        response = self.client.get(self.url)
        self.assertEqual(self._ids(response), [self.lt.id])

    def test_type_special_shows_special_and_blog_only(self):
        response = self.client.get(self.url, {'type': 'special'})

        detail_ids = self._ids(response)
        self.assertCountEqual(detail_ids, [self.special.id, self.blog.id])
        # チップ文言ではなくカードの種別バッジであることを確かめる
        self.assertContains(response, '>特別企画</span>')
        self.assertContains(response, '>ブログ</span>')


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class PresentationListVisibilityTests(TestCase):
    """未承認集会の発表を公開一覧に出さない。"""

    def setUp(self):
        cache.clear()
        self.url = reverse('event:detail_history')

        approved_community = make_community(name='承認済み集会')
        pending_community = make_community(name='未承認集会', status='pending')

        self.visible = make_event_detail(
            make_event(approved_community, event_date=date(2026, 5, 10)),
            status='approved', theme='見える発表', contents='本文',
        )
        self.hidden = make_event_detail(
            make_event(pending_community, event_date=date(2026, 5, 10)),
            status='approved', theme='見えない発表', contents='本文',
        )

    def test_pending_community_details_are_hidden(self):
        response = self.client.get(self.url, {'view': 'all'})

        detail_ids = [detail.id for detail in response.context['event_details']]
        self.assertIn(self.visible.id, detail_ids)
        self.assertNotIn(self.hidden.id, detail_ids)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class PresentationListSummaryAndSeoTests(TestCase):
    """活動サマリーと構造化データ。"""

    def setUp(self):
        cache.clear()
        self.url = reverse('event:detail_history')

        community_a = make_community(name='サマリー集会A')
        community_b = make_community(name='サマリー集会B')
        event_a = make_event(community_a, event_date=date(2026, 5, 10))
        event_b = make_event(community_b, event_date=date(2026, 5, 11))

        make_event_detail(event_a, status='approved', speaker='のり', theme='A1', contents='本文')
        make_event_detail(event_a, status='approved', speaker='のり', theme='A2')
        make_event_detail(event_b, status='approved', speaker='たろう', theme='B1', contents='本文')
        make_event_detail(event_b, status='pending', speaker='ペンディング', theme='B2')

    def test_summary_counts_approved_lt_only(self):
        response = self.client.get(self.url)

        summary = response.context['summary']
        self.assertEqual(summary['community_count'], 2)
        self.assertEqual(summary['presentation_count'], 3)
        self.assertEqual(summary['speaker_count'], 2)

    def test_summary_is_independent_of_filters(self):
        response = self.client.get(self.url, {'community_name': 'サマリー集会A'})

        self.assertEqual(response.context['summary']['presentation_count'], 3)

    def test_structured_data_contains_breadcrumb_and_collection(self):
        response = self.client.get(self.url)

        payload = json.loads(response.context['structured_data_json'])
        types = [entry['@type'] for entry in payload]
        self.assertEqual(types, ['BreadcrumbList', 'CollectionPage'])
        self.assertEqual(
            len(payload[1]['mainEntity']['itemListElement']),
            len(response.context['event_details']),
        )

    def test_meta_description_includes_summary_numbers(self):
        response = self.client.get(self.url)

        self.assertContains(response, '発表3件')


class EventDetailMaterialsTests(TestCase):
    """materials_q() と has_materials の定義が揃っていること。"""

    def setUp(self):
        cache.clear()
        self.community = make_community(name='素材判定集会')
        self.event = make_event(self.community, event_date=date(2026, 5, 10))

    def test_queryset_and_property_agree(self):
        with_contents = make_event_detail(self.event, status='approved', theme='本文', contents='あり')
        without = make_event_detail(self.event, status='approved', theme='なし')

        matched = set(
            EventDetail.objects.filter(EventDetail.materials_q()).values_list('id', flat=True)
        )

        self.assertEqual(matched, {with_contents.id})
        self.assertTrue(with_contents.has_materials)
        self.assertFalse(without.has_materials)


class EventDetailExcerptTests(TestCase):
    """get_excerpt の抜粋生成。"""

    def setUp(self):
        self.community = make_community(name='抜粋集会')
        self.event = make_event(self.community, event_date=date(2026, 5, 10))

    def _detail(self, **extra):
        return make_event_detail(self.event, status='approved', theme='抜粋テスト', **extra)

    def test_meta_description_takes_priority(self):
        detail = self._detail(meta_description='要約テキスト', contents='# 本文')

        self.assertEqual(detail.get_excerpt(), '要約テキスト')

    def test_markdown_symbols_are_removed(self):
        detail = self._detail(
            contents='# 見出し\n\n**強調**と[リンク](https://example.com)と`code`。',
        )

        excerpt = detail.get_excerpt()
        self.assertNotIn('#', excerpt)
        self.assertNotIn('**', excerpt)
        self.assertNotIn('https://example.com', excerpt)
        self.assertIn('リンク', excerpt)
        self.assertIn('強調', excerpt)

    def test_snake_case_identifiers_survive(self):
        """_ は強調記法のときだけ落とし、snake_case を壊さない"""
        detail = self._detail(contents='_強調_ しつつ event_detail を残す。')

        excerpt = detail.get_excerpt()
        self.assertIn('event_detail', excerpt)
        self.assertNotIn('_強調_', excerpt)

    def test_excerpt_is_truncated(self):
        detail = self._detail(contents='あ' * 300)

        excerpt = detail.get_excerpt(length=20)
        self.assertEqual(len(excerpt), 20)
        self.assertTrue(excerpt.endswith('…'))

    def test_excerpt_is_empty_without_source(self):
        detail = self._detail()

        self.assertEqual(detail.get_excerpt(), '')
