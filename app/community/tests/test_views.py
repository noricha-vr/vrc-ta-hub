import logging
import re
import shutil
import tempfile
from datetime import date, timedelta, time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from django.http import QueryDict
from django.db import connection
from django.test import TestCase, Client, RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone
from django.utils.html import strip_tags

from community.views.public import CommunityListView
from community.models import Community, CommunityMember
from event.models import Event, EventDetail
from tests.factories import make_community, make_event, make_event_detail
from url_filters import get_filtered_url

CustomUser = get_user_model()


class TestCommunityListViewPagination(TestCase):
    def setUp(self):
        self.client = Client()
        self.community = Community.objects.create(
            name='一覧表示テスト集会',
            status='approved',
            frequency='毎週',
            organizers='テスト主催者',
            poster_image='poster/test.jpg',
        )

    def test_invalid_page_query_redirects_to_first_page(self):
        response = self.client.get(
            reverse('community:list'),
            {'page': "1'", 'query': '一覧'},
        )

        self.assertRedirects(
            response,
            "/community/list/?page=1&query=%E4%B8%80%E8%A6%A7",
        )

    def test_out_of_range_page_redirects_to_first_page(self):
        response = self.client.get(reverse('community:list'), {'page': '999'})

        self.assertRedirects(response, '/community/list/?page=1')

    def test_last_page_query_is_accepted(self):
        response = self.client.get(reverse('community:list'), {'page': 'last'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.community.name)

    def test_poster_click_comment_is_not_rendered(self):
        response = self.client.get(reverse('community:list'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '集会一覧のポスタークリック')
        self.assertNotContains(response, 'poster_click で計測')

    def test_malformed_amp_params_are_not_propagated(self):
        response = self.client.get(
            reverse('community:list'),
            {
                'page': '1',
                'amp;weekdays': 'Fri',
                'amp;amp;tags': 'academic',
                'query': '一覧',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_weekdays'], [])
        self.assertEqual(response.context['selected_tags'], [])
        self.assertNotIn('amp', response.context['current_query_params'])

    def test_get_filtered_url_removes_existing_value(self):
        params = QueryDict('', mutable=True)
        params.setlist('weekdays', ['Sat', 'Fri'])

        url = get_filtered_url('/community/list/', params, 'weekdays', 'Sat')

        self.assertEqual(url, '/community/list/?weekdays=Fri')

    def test_get_filtered_url_returns_base_url_when_last_value_removed(self):
        params = QueryDict('', mutable=True)
        params.setlist('weekdays', ['Sat'])

        url = get_filtered_url('/community/list/', params, 'weekdays', 'Sat')

        self.assertEqual(url, '/community/list/')

    def test_count_query_does_not_group_by_events(self):
        Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(21, 0),
            duration=60,
        )
        request = RequestFactory().get(reverse('community:list'))
        view = CommunityListView()
        view.setup(request)

        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(view.get_queryset().count(), 1)

        count_sql = ' '.join(
            query['sql'] for query in captured.captured_queries
            if 'COUNT' in query['sql'].upper()
        ).upper()
        self.assertIn('COUNT', count_sql)
        self.assertNotIn('GROUP BY', count_sql)


class AcceptViewTest(TestCase):
    def setUp(self):
        # 管理者ユーザー（承認権限あり）
        self.admin_user = CustomUser.objects.create_superuser(
            email='admin@example.com',
            password='testpass123',
            user_name='管理者ユーザー'
        )
        self.admin_community = Community.objects.create(
            name='管理者集会',
            status='approved'
        )

        # 未承認ユーザー（承認申請者）
        self.pending_user = CustomUser.objects.create_user(
            email='pending@example.com',
            password='testpass123',
            user_name='未承認ユーザー'
        )
        self.pending_community = Community.objects.create(
            name='未承認集会',
            status='pending',
            poster_image='poster/pending-test.jpg',
        )
        # オーナーとしてCommunityMemberを作成
        CommunityMember.objects.create(
            community=self.pending_community,
            user=self.pending_user,
            role=CommunityMember.Role.OWNER
        )

        self.client = Client()

    def test_accept_community_sends_email(self):
        """集会承認時にメールが送信されることをテスト"""
        # 管理者ユーザーでログイン
        self.client.force_login(self.admin_user)

        # 未承認集会を承認（pkパラメータを指定）
        response = self.client.post(
            reverse('community:accept', kwargs={'pk': self.pending_community.pk})
        )

        # リダイレクトを確認
        self.assertRedirects(response, reverse('community:waiting_list'))

        # メールが送信されたことを確認
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]

        # メールの内容を確認
        self.assertEqual(email.subject, f'{self.pending_community.name}が承認されました')
        self.assertEqual(email.to, [self.pending_user.email])
        
        # HTMLメールの内容を確認
        self.assertTrue(hasattr(email, 'alternatives'))
        html_content = email.alternatives[0][0]

        # メールの本文に必要な情報が含まれていることを確認
        self.assertIn(self.pending_community.name, html_content)
        self.assertIn('my_list', html_content)
        self.assertIn('開催日を登録', html_content)
        self.assertIn('<h1>', html_content)  # HTML形式であることを確認

    def test_waiting_list_uses_compact_rows_with_detail_link(self):
        """承認待ち一覧はポスターを出さず、集会名と詳細への導線を出す。"""
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse('community:waiting_list'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'poster/pending-test.jpg')
        self.assertContains(response, self.pending_community.name)
        self.assertContains(response, reverse('community:detail', args=[self.pending_community.pk]))
        self.assertContains(response, '詳細')




class CommunityDetailViewEventThemeDisplayTest(TestCase):
    """CommunityDetailViewのテーマ表示テスト

    EventDetailのtitleプロパティを使用して表示。
    h1があればh1を表示し、なければthemeを表示する。
    """

    DURATION_MINUTES = 120  # イベント時間（分）

    def setUp(self):
        self.client = Client()

        # テスト用ユーザー
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='testpass123',
            user_name='テストユーザー'
        )

        # テスト用集会
        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週',
            organizers='テスト主催者'
        )
        # オーナーとしてCommunityMemberを作成
        CommunityMember.objects.create(
            community=self.community,
            user=self.user,
            role=CommunityMember.Role.OWNER
        )

        # 将来のイベント（開催日程セクションに表示される）
        future_date = date.today() + timedelta(days=7)
        self.future_event = Event.objects.create(
            community=self.community,
            date=future_date,
            start_time=time(21, 0),
            duration=self.DURATION_MINUTES
        )

        # 過去のイベント（発表履歴セクションに表示される）
        past_date = date.today() - timedelta(days=7)
        self.past_event = Event.objects.create(
            community=self.community,
            date=past_date,
            start_time=time(21, 0),
            duration=self.DURATION_MINUTES
        )

    def test_theme_blog_shows_h1_in_scheduled_events(self):
        """開催日程: BLOGタイプではthemeにh1がコピーされて表示される"""
        # フォーム保存時にh1がthemeにコピーされる仕様のため、
        # テストデータも同様にthemeにh1の値をセット
        EventDetail.objects.create(
            event=self.future_event,
            speaker='発表者A',
            theme='実際のブログタイトル',  # フォームでh1からコピーされた値
            h1='実際のブログタイトル'
        )

        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        # themeにコピーされたh1の値が表示される
        self.assertContains(response, '実際のブログタイトル')

    def test_normal_theme_shows_theme_in_scheduled_events(self):
        """開催日程: 通常のthemeはそのまま表示される"""
        # themeが通常の値
        EventDetail.objects.create(
            event=self.future_event,
            speaker='発表者B',
            theme='ゆれをながめる',
            h1=''
        )

        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ゆれをながめる')

    def test_theme_blog_shows_h1_in_past_events(self):
        """発表履歴: BLOGタイプではthemeにh1がコピーされて表示される"""
        # フォーム保存時にh1がthemeにコピーされる仕様のため、
        # テストデータも同様にthemeにh1の値をセット
        EventDetail.objects.create(
            event=self.past_event,
            speaker='発表者C',
            theme='過去のブログタイトル',  # フォームでh1からコピーされた値
            h1='過去のブログタイトル'
        )

        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        # themeにコピーされたh1の値が表示される
        self.assertContains(response, '過去のブログタイトル')

    def test_normal_theme_shows_theme_in_past_events(self):
        """発表履歴: 通常のthemeはそのまま表示される"""
        # themeが通常の値
        EventDetail.objects.create(
            event=self.past_event,
            speaker='発表者D',
            theme='VRの未来を語る',
            h1=''
        )

        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'VRの未来を語る')

    def test_scheduled_events_section_hidden_when_empty(self):
        """開催日程が0件の場合、開催日程セクションが表示されない"""
        # future_event を削除して開催予定を空にする
        self.future_event.delete()
        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'の開催日程')

    def test_past_events_section_hidden_when_empty(self):
        """発表履歴が0件の場合、発表履歴セクションが表示されない"""
        # past_event に EventDetail を作成しない状態でアクセス
        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'の発表履歴')

    def test_theme_blog_without_h1_shows_blog(self):
        """themeが「Blog」でh1が空の場合はBlogが表示される"""
        # themeが「Blog」、h1が空（フォールバック）
        EventDetail.objects.create(
            event=self.future_event,
            speaker='発表者E',
            theme='Blog',
            h1=''
        )

        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        # h1が空の場合はフォールバックとしてBlogが表示される
        self.assertContains(response, 'Blog')


class PosterDownloadViewTest(TestCase):
    """ポスター画像ダウンロードのテスト"""

    poster_bytes = b'original poster bytes'

    def setUp(self):
        self.client = Client()
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(
            MEDIA_ROOT=self.media_root,
            STORAGES={
                'default': {
                    'BACKEND': 'django.core.files.storage.FileSystemStorage',
                },
                'staticfiles': {
                    'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
                },
            },
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(shutil.rmtree, self.media_root)

        poster_path = Path(self.media_root) / 'poster' / 'test-poster.gif'
        poster_path.parent.mkdir(parents=True, exist_ok=True)
        poster_path.write_bytes(self.poster_bytes)

        self.community = Community.objects.create(
            name='ポスターダウンロード集会',
            status='approved',
            frequency='毎週',
            organizers='テスト主催者',
            poster_image='poster/test-poster.gif',
            allow_poster_repost=True,
        )

    def test_detail_shows_poster_download_link_when_repost_allowed(self):
        response = self.client.get(
            reverse('community:detail', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse('community:poster_download', kwargs={'pk': self.community.pk}),
        )
        self.assertContains(response, 'bi-download')
        self.assertContains(response, 'mw-100 text-wrap text-start')

    def test_download_returns_original_poster_as_attachment(self):
        response = self.client.get(
            reverse('community:poster_download', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), self.poster_bytes)
        self.assertEqual(response['Content-Type'], 'image/gif')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('test-poster.gif', response['Content-Disposition'])

    def test_download_404_when_repost_not_allowed(self):
        self.community.allow_poster_repost = False
        self.community.save(update_fields=['allow_poster_repost'])

        response = self.client.get(
            reverse('community:poster_download', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 404)

    def test_download_404_when_community_is_not_approved(self):
        self.community.status = 'pending'
        self.community.save(update_fields=['status'])

        response = self.client.get(
            reverse('community:poster_download', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 404)


class CommunityDetailViewBlogSpecialSectionTest(TestCase):
    """コミュニティ詳細ページのBLOG/SPECIAL記事セクション表示テスト"""

    DURATION_MINUTES = 120

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            email='blogtest@example.com',
            password='testpass123',
            user_name='ブログテストユーザー'
        )
        self.community = Community.objects.create(
            name='テスト集会BS',
            status='approved',
            frequency='毎週',
            organizers='テスト主催者'
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.user,
            role=CommunityMember.Role.OWNER
        )
        self.past_event = Event.objects.create(
            community=self.community,
            date=date.today() - timedelta(days=7),
            start_time=time(21, 0),
            duration=self.DURATION_MINUTES
        )
        self.url = reverse('community:detail', kwargs={'pk': self.community.pk})

    def test_blog_special_section_shown_when_exists(self):
        """BLOG/SPECIALがある場合、記事・特別企画セクションが表示される"""
        EventDetail.objects.create(
            event=self.past_event,
            detail_type='BLOG',
            status='approved',
            h1='テストブログ記事',
            theme='テストブログ記事',
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '記事・特別企画')
        self.assertContains(response, 'テストブログ記事')
        self.assertContains(response, 'badge bg-info')

    def test_special_type_shows_warning_badge(self):
        """SPECIALタイプは特別企画バッジで表示される"""
        EventDetail.objects.create(
            event=self.past_event,
            detail_type='SPECIAL',
            status='approved',
            h1='テスト特別企画',
            theme='Special Event',
        )
        response = self.client.get(self.url)
        self.assertContains(response, 'テスト特別企画')
        self.assertContains(response, 'badge bg-warning')

    def test_blog_special_section_hidden_when_none(self):
        """BLOG/SPECIALがない場合、セクションが表示されない"""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '記事・特別企画')

    def test_unapproved_blog_not_shown(self):
        """未承認のBLOG/SPECIALは表示されない"""
        EventDetail.objects.create(
            event=self.past_event,
            detail_type='BLOG',
            status='pending',
            h1='未承認ブログ',
            theme='未承認ブログ',
        )
        response = self.client.get(self.url)
        self.assertNotContains(response, '未承認ブログ')
        self.assertNotContains(response, '記事・特別企画')

    def test_blog_excluded_from_lt_table(self):
        """BLOGタイプは発表履歴カードに表示されない"""
        EventDetail.objects.create(
            event=self.past_event,
            detail_type='BLOG',
            status='approved',
            h1='ブログ記事タイトル',
            theme='ブログ記事タイトル',
        )
        response = self.client.get(self.url)
        self.assertEqual(list(response.context['past_presentations']), [])


@override_settings(LANGUAGE_CODE='ja')
class CommunityPastPresentationCardsTest(TestCase):
    """発表履歴は承認済みの発表単位で、開催日時付きのカードを表示する。"""

    def setUp(self):
        self.community = make_community(
            name='カード表示集会', poster_image='poster/community-only.jpg',
        )
        self.event = make_event(
            self.community, event_date=date(2020, 1, 2),
            start_time=time(20, 15), duration=90,
        )
        self.url = reverse('community:detail', args=[self.community.pk])

    def make_presentation(self, event=None, **extra):
        extra.setdefault('status', 'approved')
        return make_event_detail(event or self.event, **extra)

    def cards(self, response):
        self.assertEqual(response.status_code, 200)
        return re.findall(
            r'<article class="[^"]*ed-rel-card">.*?</article>',
            response.content.decode(), re.DOTALL,
        )

    def test_two_presentations_in_one_event_have_separate_cards(self):
        first = self.make_presentation(theme='前半の発表', start_time=time(20, 15))
        second = self.make_presentation(theme='後半の発表', start_time=time(21, 0))

        response = self.client.get(self.url)

        self.assertEqual(list(response.context['past_presentations']), [second, first])
        cards = self.cards(response)
        self.assertEqual(len(cards), 2)
        for card, detail in zip(cards, [second, first]):
            self.assertIn(detail.theme, card)
            self.assertIn(reverse('event:detail', args=[detail.pk]), card)
        self.assertContains(response, 'row row-cols-1 row-cols-md-3 g-4')
        self.assertNotIn('past_events', response.context)

    def test_only_latest_six_presentations_are_shown(self):
        details = []
        for offset in range(8):
            event = make_event(
                self.community, event_date=date(2020, 1, 3) + timedelta(days=offset),
            )
            details.append(self.make_presentation(event, theme=f'履歴の発表{offset}'))

        response = self.client.get(self.url)

        self.assertEqual(list(response.context['past_presentations']), details[:1:-1])
        self.assertEqual(len(self.cards(response)), 6)
        for detail in details[:2]:
            self.assertNotContains(response, detail.theme)

    def test_order_is_event_date_then_event_time_then_presentation_time(self):
        old = self.make_presentation(theme='以前の日付', start_time=time(23, 0))
        early_event = make_event(
            self.community, event_date=date(2020, 1, 3), start_time=time(18, 0),
        )
        late_event = make_event(
            self.community, event_date=date(2020, 1, 3), start_time=time(21, 0),
        )
        early = self.make_presentation(early_event, theme='同日の早い回')
        late_first = self.make_presentation(
            late_event, theme='遅い回の前半', start_time=time(21, 0),
        )
        late_last = self.make_presentation(
            late_event, theme='遅い回の後半', start_time=time(21, 30),
        )

        response = self.client.get(self.url)

        expected = [late_last, late_first, early, old]
        self.assertEqual(list(response.context['past_presentations']), expected)
        for card, detail in zip(self.cards(response), expected):
            self.assertIn(detail.theme, card)

    def test_non_public_non_presentation_and_empty_theme_are_excluded(self):
        visible = self.make_presentation(theme='表示する発表')
        self.make_presentation(theme='ブログ', detail_type='BLOG')
        self.make_presentation(theme='特別企画', detail_type='SPECIAL')
        self.make_presentation(theme='承認待ち', status='pending')
        self.make_presentation(theme='却下済み', status='rejected')
        deleted = self.make_presentation(theme='削除済み')
        deleted.delete()
        self.make_presentation(theme='')
        future = make_event(self.community, event_date=date.today() + timedelta(days=7))
        self.make_presentation(future, theme='開催予定')
        other_event = make_event(make_community(name='別の集会'), event_date=self.event.date)
        self.make_presentation(other_event, theme='別の集会の発表')

        response = self.client.get(self.url)

        self.assertEqual(list(response.context['past_presentations']), [visible])
        self.assertEqual(len(self.cards(response)), 1)

    def test_every_card_shows_weekday_and_event_start_and_end_times(self):
        for index in range(2):
            self.make_presentation(
                theme=f'日時確認{index}', start_time=time(21, index * 15), duration=15,
            )

        cards = self.cards(self.client.get(self.url))

        self.assertEqual(len(cards), 2)
        for card in cards:
            self.assertIn('2020年1月2日 (木)', card)
            self.assertIn('20:15 - 21:45', card)

    def test_thumbnail_image_takes_priority_over_video_thumbnail(self):
        detail = self.make_presentation(
            theme='画像付き発表', thumbnail_image='thumbnails/presentation.jpg',
            youtube_url='https://www.youtube.com/watch?v=abcdefghijk',
        )

        card, = self.cards(self.client.get(self.url))

        self.assertIn('<img src="', card)
        self.assertIn('thumbnails/presentation.jpg', card)
        self.assertIn('loading="lazy" decoding="async"', card)
        self.assertIn('class="ed-rel-title"', card)
        self.assertIn(reverse('event:detail', args=[detail.pk]), card)
        self.assertNotIn('mqdefault.jpg', card)
        self.assertNotIn('class="ed-titlecard"', card)

    def test_youtube_url_only_shows_video_thumbnail(self):
        self.make_presentation(youtube_url='https://youtu.be/abcdefghijk')

        card, = self.cards(self.client.get(self.url))

        self.assertIn('<img src="https://img.youtube.com/vi/abcdefghijk/mqdefault.jpg"', card)
        self.assertIn('class="ed-rel-title"', card)
        self.assertNotIn('class="ed-titlecard"', card)

    def test_without_thumbnail_title_appears_once_with_accessible_body_link(self):
        detail = self.make_presentation(theme='画像のない発表', speaker='発表者名')

        card, = self.cards(self.client.get(self.url))

        self.assertIn('class="ed-titlecard"', card)
        self.assertEqual(strip_tags(card).count(detail.theme), 1)
        body = card.split('<div class="card-body">', 1)[1]
        self.assertIn(
            f'<a class="stretched-link" href="{reverse("event:detail", args=[detail.pk])}" '
            f'aria-label="{detail.theme}"></a>', body,
        )
        self.assertIn(detail.speaker, card)
        self.assertNotIn('class="ed-rel-title"', card)
        self.assertNotIn('<img', card)
        self.assertNotIn(self.community.poster_image.url, card)
        self.assertNotIn('poster/community-only.jpg', card)

    def test_more_link_keeps_community_history_filter(self):
        self.make_presentation()

        response = self.client.get(self.url)

        history_url = reverse('event:detail_history')
        links = re.findall(r'href="([^"]+)"[^>]*>もっと見る</a>', response.content.decode())
        self.assertEqual(len(links), 1)
        self.assertEqual(urlsplit(links[0]).path, history_url)
        self.assertEqual(parse_qs(urlsplit(links[0]).query), {'community_name': [self.community.name]})

    def test_today_row_uses_info_color_instead_of_danger(self):
        """開催日程の今日の行は、危険色の赤ではなく水色で示す"""
        make_event(self.community, event_date=timezone.localdate())

        response = self.client.get(self.url)

        self.assertContains(response, 'class="table-info"')
        self.assertNotContains(response, 'table-danger')

    def test_history_section_hidden_without_presentations(self):
        response = self.client.get(self.url)

        self.assertNotContains(response, f'{self.community.name}の発表履歴')
        self.assertEqual(self.cards(response), [])

    def test_six_cards_do_not_add_queries_per_presentation(self):
        # DEBUGログのContext reprによる関連取得を除き、実際の取得・描画を測る。
        template_logger = logging.getLogger('django.template')
        original_level = template_logger.level
        template_logger.setLevel(logging.INFO)
        self.addCleanup(template_logger.setLevel, original_level)
        self.make_presentation(theme='最初の発表')
        self.client.get(self.url)
        with CaptureQueriesContext(connection) as one_card_queries:
            one_card_response = self.client.get(self.url)
        self.assertEqual(len(self.cards(one_card_response)), 1)

        for offset in range(5):
            event = make_event(
                self.community, event_date=date(2020, 1, 3) + timedelta(days=offset),
            )
            self.make_presentation(event, theme=f'追加の発表{offset}')

        with CaptureQueriesContext(connection) as six_card_queries:
            six_card_response = self.client.get(self.url)

        self.assertEqual(len(self.cards(six_card_response)), 6)
        self.assertEqual(
            len(six_card_queries), len(one_card_queries),
            '\n'.join(query['sql'] for query in six_card_queries.captured_queries),
        )


class CommunityDetailViewLtApplicationSectionTest(TestCase):
    """CommunityDetailViewのLT申請セクション表示テスト"""

    LOGIN_NOTICE = '申請にはログインが必要です'

    def setUp(self):
        self.client = Client()

        # テスト用ユーザー
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='testpass123',
            user_name='テストユーザー'
        )

        # テスト用集会（承認済み、LT申請受付ON）
        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週',
            organizers='テスト主催者',
            accepts_lt_application=True
        )
        # オーナーとしてCommunityMemberを作成
        CommunityMember.objects.create(
            community=self.community,
            user=self.user,
            role=CommunityMember.Role.OWNER
        )

        self.detail_url = reverse(
            'community:detail', kwargs={'pk': self.community.pk}
        )
        self.apply_url = reverse(
            'event:lt_application_create',
            kwargs={'community_pk': self.community.pk},
        )

    def test_lt_section_shown_when_authenticated_and_approved_and_accepts_lt(self):
        """ログイン済み・承認済み・LT受付ONの場合、LT申請セクションが表示される"""
        self.client.force_login(self.user)
        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.apply_url)

    def test_login_notice_not_shown_when_authenticated(self):
        """ログイン済みの場合、ログインが必要な旨の補足文は表示されない"""
        self.client.force_login(self.user)
        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.LOGIN_NOTICE)

    def test_lt_section_shown_when_not_authenticated(self):
        """未ログインでも申請導線と補足文が表示される（Issue #569）"""
        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.apply_url)
        self.assertContains(response, self.LOGIN_NOTICE)

    def test_lt_section_not_shown_when_not_approved(self):
        """未承認集会の場合、詳細ページ自体が閲覧できない（superuserのみ閲覧可）"""
        self.community.status = 'pending'
        self.community.save()

        self.client.force_login(self.user)
        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, 404)

    def test_lt_section_not_shown_when_accepts_lt_is_false(self):
        """LT受付OFFの場合、未ログインでもLT申請セクションは表示されない"""
        self.community.accepts_lt_application = False
        self.community.save()

        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.apply_url)
        self.assertNotContains(response, self.LOGIN_NOTICE)

    def test_anonymous_apply_shows_login_link_with_next(self):
        """受付中の集会は公開案内から next 付きのログインへ誘導する"""
        response = self.client.get(self.apply_url)

        self.assertEqual(response.status_code, 200)
        login_url = response.context['login_url']
        self.assertEqual(urlsplit(login_url).path, '/account/login/')
        self.assertEqual(parse_qs(urlsplit(login_url).query)['next'], [self.apply_url])
        self.assertNotContains(response, 'name="theme"')


class CommunityDetailArchiveNoticeTest(TestCase):
    """コミュニティ詳細ページのアーカイブ表示テスト"""

    def setUp(self):
        self.client = Client()
        self.community = Community.objects.create(
            name='アーカイブ表示テスト集会',
            status='approved',
            frequency='毎週',
            organizers='テスト主催者',
        )
        self.future_event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(21, 0),
            duration=60,
        )
        self.url = reverse('community:detail', kwargs={'pk': self.community.pk})

    def test_active_community_shows_report_button_without_archive_notice(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'この集会は終了しています')
        self.assertContains(response, 'この集会が開催されていないかもしれない')
        self.assertContains(response, 'reportModal')
        self.assertContains(response, 'アーカイブ表示テスト集会の開催日程')

    def test_archived_community_shows_notice_and_hides_report_button(self):
        self.community.end_at = date.today() - timedelta(days=1)
        self.community.save(update_fields=['end_at'])

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'この集会は終了しています')
        self.assertNotContains(response, 'この集会が開催されていないかもしれない')
        self.assertNotContains(response, 'reportModal')
        self.assertNotContains(response, 'アーカイブ表示テスト集会の開催日程')


class CommunityUpdateViewPromotionBannerTest(TestCase):
    """CommunityUpdateViewのプロモーションバナー表示テスト"""

    def setUp(self):
        self.client = Client()

        # テスト用ユーザー
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='testpass123',
            user_name='テストユーザー'
        )

        # テスト用集会（承認済み）
        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週',
            organizers='テスト主催者'
        )
        # オーナーとしてCommunityMemberを作成
        CommunityMember.objects.create(
            community=self.community,
            user=self.user,
            role=CommunityMember.Role.OWNER
        )

    def test_promotion_banner_not_displayed_on_update_page(self):
        """集会編集ページにプロモーションバナーを表示しないこと"""
        self.client.force_login(self.user)

        # アクティブ集会を設定するためセッションを設定
        session = self.client.session
        session['active_community_id'] = self.community.pk
        session.save()

        response = self.client.get(
            reverse('community:update')
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Hubを広めるご協力のお願い')
        self.assertNotContains(response, 'ワールドへのポスター掲示やDiscordでの紹介')
        self.assertNotContains(response, '今後表示しない')


class CommunityDetailAdminCleanupButtonTest(TestCase):
    """コミュニティ詳細の管理者ワンクリック修復ボタン表示テスト"""

    def setUp(self):
        self.client = Client()

        self.admin = CustomUser.objects.create_superuser(
            email='admin-cleanup@example.com',
            password='testpass123',
            user_name='管理者メンテ'
        )
        self.normal_user = CustomUser.objects.create_user(
            email='normal@example.com',
            password='testpass123',
            user_name='一般ユーザー'
        )
        self.community = Community.objects.create(
            name='ボタン表示テスト集会',
            status='approved',
            frequency='毎週',
            organizers='テスト主催'
        )

    def test_superuser_sees_admin_cleanup_button(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('community:detail', kwargs={'pk': self.community.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '管理者メンテナンス（ワンクリック）')
        self.assertContains(response, 'adminCleanupModal')
        self.assertContains(response, '管理者ワンクリック修復を実行')

    def test_non_superuser_cannot_see_admin_cleanup_button(self):
        self.client.force_login(self.normal_user)
        response = self.client.get(reverse('community:detail', kwargs={'pk': self.community.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '管理者メンテナンス（ワンクリック）')
        self.assertNotContains(response, 'adminCleanupModal')
