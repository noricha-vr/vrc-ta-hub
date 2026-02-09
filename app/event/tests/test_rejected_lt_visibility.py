"""却下済み・未承認のLT申請が公開ビューに表示されない再発防止テスト.

修正背景:
    status='approved' フィルタの欠落により、pending/rejected状態の
    EventDetailがイベント一覧・カレンダー・Twitter・APIに漏洩していた。
"""
from datetime import date, time, timedelta
from urllib.parse import unquote

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, RequestFactory, override_settings
from django.urls import reverse

from community.models import Community, CommunityMember
from event.models import Event, EventDetail

User = get_user_model()


class EventListViewFilterTest(TestCase):
    """EventListView で承認済みのEventDetailのみ表示されるテスト"""

    def setUp(self):
        self.client = Client()

        self.community = Community.objects.create(
            name='Test Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
        )

        self.event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
        )

        self.approved_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Approved Theme',
            speaker='Approved Speaker',
            status='approved',
            duration=15,
            start_time=time(22, 0),
        )

        self.pending_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Pending Theme',
            speaker='Pending Speaker',
            status='pending',
            duration=15,
            start_time=time(22, 15),
        )

        self.rejected_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Rejected Theme',
            speaker='Rejected Speaker',
            status='rejected',
            rejection_reason='Not suitable',
            duration=15,
            start_time=time(22, 30),
        )

    def test_event_list_only_shows_approved_details(self):
        """イベント一覧で承認済みのEventDetailのみprefetchされる"""
        url = reverse('event:list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # コンテキストからイベントを取得
        events = response.context['events']

        for event in events:
            if event.id == self.event.id:
                # prefetch_relatedでロード済みのdetailsを確認
                details = list(event.details.all())
                detail_statuses = [d.status for d in details]

                self.assertIn('approved', detail_statuses)
                self.assertNotIn('pending', detail_statuses)
                self.assertNotIn('rejected', detail_statuses)
                break
        else:
            self.fail('テスト対象のイベントが一覧に見つからない')


class EventDetailAPIPublicViewSetFilterTest(TestCase):
    """公開API (EventDetailViewSet) で承認済みのEventDetailのみ返るテスト"""

    def setUp(self):
        self.client = Client()

        self.community = Community.objects.create(
            name='API Test Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
        )

        self.event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
        )

        self.approved_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Approved API Theme',
            speaker='Approved API Speaker',
            status='approved',
            duration=15,
            start_time=time(22, 0),
        )

        self.pending_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Pending API Theme',
            speaker='Pending API Speaker',
            status='pending',
            duration=15,
            start_time=time(22, 15),
        )

        self.rejected_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Rejected API Theme',
            speaker='Rejected API Speaker',
            status='rejected',
            rejection_reason='Not suitable',
            duration=15,
            start_time=time(22, 30),
        )

    def test_public_api_only_returns_approved_details(self):
        """公開APIで承認済みのEventDetailのみ返される"""
        url = reverse('eventdetail-list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        data = response.json()
        # ページネーションありの場合はresultsキー、なしの場合は直接リスト
        results = data['results'] if isinstance(data, dict) and 'results' in data else data

        returned_ids = [item['id'] for item in results]

        self.assertIn(self.approved_detail.id, returned_ids)
        self.assertNotIn(self.pending_detail.id, returned_ids)
        self.assertNotIn(self.rejected_detail.id, returned_ids)

    def test_public_api_does_not_return_pending_detail_by_id(self):
        """公開APIでpendingのEventDetailを個別取得できない"""
        url = reverse('eventdetail-detail', kwargs={'pk': self.pending_detail.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 404)

    def test_public_api_does_not_return_rejected_detail_by_id(self):
        """公開APIでrejectedのEventDetailを個別取得できない"""
        url = reverse('eventdetail-detail', kwargs={'pk': self.rejected_detail.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 404)


class TwitterUtilsFilterTest(TestCase):
    """twitter/utils.py の format_event_info で承認済みのみ取得するテスト"""

    def setUp(self):
        self.community = Community.objects.create(
            name='Twitter Test Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
        )

        self.event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
        )

        self.approved_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Approved Twitter Theme',
            speaker='Approved Twitter Speaker',
            status='approved',
            duration=15,
            start_time=time(22, 0),
        )

        self.pending_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Pending Twitter Theme',
            speaker='Pending Twitter Speaker',
            status='pending',
            duration=15,
            start_time=time(22, 15),
        )

        self.rejected_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Rejected Twitter Theme',
            speaker='Rejected Twitter Speaker',
            status='rejected',
            rejection_reason='Not suitable',
            duration=15,
            start_time=time(22, 30),
        )

    def test_format_event_info_only_includes_approved(self):
        """format_event_info は承認済みのEventDetailのみ含む"""
        from twitter.utils import format_event_info

        event_info = format_event_info(self.event)
        details_text = event_info['details']

        self.assertIn('Approved Twitter Theme', details_text)
        self.assertNotIn('Pending Twitter Theme', details_text)
        self.assertNotIn('Rejected Twitter Theme', details_text)


class CalendarUtilsFilterTest(TestCase):
    """calendar_utils.py の generate_google_calendar_url で承認済みのみ含むテスト"""

    def setUp(self):
        self.community = Community.objects.create(
            name='Calendar Test Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
        )

        self.event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
        )

        self.approved_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Approved Calendar Theme',
            speaker='Approved Calendar Speaker',
            status='approved',
            duration=15,
            start_time=time(22, 0),
        )

        self.rejected_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Rejected Calendar Theme',
            speaker='Rejected Calendar Speaker',
            status='rejected',
            rejection_reason='Not suitable',
            duration=15,
            start_time=time(22, 30),
        )

    def test_google_calendar_url_only_includes_approved_details(self):
        """GoogleカレンダーURL生成時に承認済みの発表情報のみ含まれる"""
        from event_calendar.calendar_utils import generate_google_calendar_url

        factory = RequestFactory()
        request = factory.get('/')
        request.META['SERVER_NAME'] = 'testserver'
        request.META['SERVER_PORT'] = '80'

        url = generate_google_calendar_url(request, self.event)
        decoded_url = unquote(url)

        self.assertIn('Approved Calendar Speaker', decoded_url)
        self.assertIn('Approved Calendar Theme', decoded_url)
        self.assertNotIn('Rejected Calendar Speaker', decoded_url)
        self.assertNotIn('Rejected Calendar Theme', decoded_url)


def _create_test_image():
    """テスト用の最小限のPNG画像バイナリを生成する"""
    # 1x1ピクセルの最小PNG
    import struct
    import zlib

    def _chunk(chunk_type, data):
        c = chunk_type + data
        crc = struct.pack('>I', zlib.crc32(c) & 0xffffffff)
        return struct.pack('>I', len(data)) + c + crc

    signature = b'\x89PNG\r\n\x1a\n'
    ihdr_data = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
    raw_data = b'\x00\xff\x00\x00'
    idat_data = zlib.compress(raw_data)

    return signature + _chunk(b'IHDR', ihdr_data) + _chunk(b'IDAT', idat_data) + _chunk(b'IEND', b'')


class IndexViewLTFilterTest(TestCase):
    """トップページのLT一覧で承認済みのEventDetailのみ表示されるテスト"""

    def setUp(self):
        self.client = Client()
        cache.clear()

        image_content = _create_test_image()
        poster = SimpleUploadedFile('test.png', image_content, content_type='image/png')

        self.community = Community.objects.create(
            name='Index LT Test Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
            poster_image=poster,
        )

        self.event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=3),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
        )

        self.approved_lt = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Approved Index LT',
            speaker='Approved Speaker',
            status='approved',
            duration=15,
            start_time=time(22, 0),
        )

        self.rejected_lt = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Rejected Index LT',
            speaker='Rejected Speaker',
            status='rejected',
            rejection_reason='Not suitable',
            duration=15,
            start_time=time(22, 15),
        )

        self.pending_lt = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Pending Index LT',
            speaker='Pending Speaker',
            status='pending',
            duration=15,
            start_time=time(22, 30),
        )

    def tearDown(self):
        cache.clear()

    def test_index_lt_list_only_shows_approved(self):
        """トップページのLT一覧で承認済みのみ表示される"""
        url = reverse('ta_hub:index')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        details = response.context.get('upcoming_event_details', [])
        # details はdict形式の場合もあるため、speaker キーで判定
        speakers = []
        for d in details:
            if isinstance(d, dict):
                speakers.append(d.get('speaker', ''))
            else:
                speakers.append(getattr(d, 'speaker', ''))

        self.assertIn('Approved Speaker', speakers)
        self.assertNotIn('Rejected Speaker', speakers)
        self.assertNotIn('Pending Speaker', speakers)


class IndexViewSpecialFilterTest(TestCase):
    """トップページの特別企画で承認済みのEventDetailのみ表示されるテスト"""

    def setUp(self):
        self.client = Client()
        cache.clear()

        image_content = _create_test_image()
        poster = SimpleUploadedFile('test.png', image_content, content_type='image/png')

        self.community = Community.objects.create(
            name='Index Special Test Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
            poster_image=poster,
        )

        self.event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=3),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
        )

        self.approved_special = EventDetail.objects.create(
            event=self.event,
            detail_type='SPECIAL',
            theme='Approved Special Event',
            status='approved',
            duration=60,
            start_time=time(22, 0),
        )

        self.rejected_special = EventDetail.objects.create(
            event=self.event,
            detail_type='SPECIAL',
            theme='Rejected Special Event',
            status='rejected',
            rejection_reason='Not suitable',
            duration=60,
            start_time=time(22, 0),
        )

    def tearDown(self):
        cache.clear()

    def test_index_special_events_only_shows_approved(self):
        """トップページの特別企画で承認済みのみ表示される"""
        url = reverse('ta_hub:index')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        specials = response.context.get('special_events', [])
        themes = []
        for s in specials:
            if isinstance(s, dict):
                themes.append(s.get('theme', ''))
            else:
                themes.append(getattr(s, 'theme', ''))

        self.assertIn('Approved Special Event', themes)
        self.assertNotIn('Rejected Special Event', themes)


class EventDetailViewAccessTest(TestCase):
    """EventDetailView で未認証ユーザーがrejected/pendingの詳細にアクセスすると404になるテスト"""

    def setUp(self):
        self.client = Client()

        self.community = Community.objects.create(
            name='Detail Access Test Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
        )

        self.event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
        )

        self.approved_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Approved Detail',
            speaker='Speaker A',
            status='approved',
            duration=15,
            start_time=time(22, 0),
        )

        self.rejected_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Rejected Detail',
            speaker='Speaker B',
            status='rejected',
            rejection_reason='Not suitable',
            duration=15,
            start_time=time(22, 15),
        )

        self.pending_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Pending Detail',
            speaker='Speaker C',
            status='pending',
            duration=15,
            start_time=time(22, 30),
        )

        # スーパーユーザーを作成
        self.superuser = User.objects.create_superuser(
            user_name='admin_test',
            email='admin@test.com',
            password='testpass123',
        )

        # コミュニティ管理者を作成
        self.community_manager = User.objects.create_user(
            user_name='community_mgr',
            email='mgr@test.com',
            password='testpass123',
        )
        from allauth.socialaccount.models import SocialAccount
        SocialAccount.objects.create(user=self.community_manager, provider='discord', uid='discord_mgr')
        CommunityMember.objects.create(
            community=self.community,
            user=self.community_manager,
            role=CommunityMember.Role.STAFF,
        )

        # 一般ユーザー（管理者でない）を作成
        self.normal_user = User.objects.create_user(
            user_name='normal_user',
            email='normal@test.com',
            password='testpass123',
        )
        SocialAccount.objects.create(user=self.normal_user, provider='discord', uid='discord_normal')

        # 申請者ユーザーを作成
        self.applicant_user = User.objects.create_user(
            user_name='applicant_user',
            email='applicant@test.com',
            password='testpass123',
        )
        SocialAccount.objects.create(user=self.applicant_user, provider='discord', uid='discord_applicant')

        # pending_detail に申請者を設定
        self.pending_detail.applicant = self.applicant_user
        self.pending_detail.save()

    def test_anonymous_can_access_approved_detail(self):
        """未認証ユーザーはapprovedの詳細にアクセスできる"""
        url = reverse('event:detail', kwargs={'pk': self.approved_detail.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_anonymous_cannot_access_rejected_detail(self):
        """未認証ユーザーはrejectedの詳細にアクセスすると404"""
        url = reverse('event:detail', kwargs={'pk': self.rejected_detail.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_anonymous_cannot_access_pending_detail(self):
        """未認証ユーザーはpendingの詳細にアクセスすると404"""
        url = reverse('event:detail', kwargs={'pk': self.pending_detail.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_superuser_can_access_rejected_detail(self):
        """スーパーユーザーはrejectedの詳細にもアクセスできる"""
        self.client.login(username='admin_test', password='testpass123')
        url = reverse('event:detail', kwargs={'pk': self.rejected_detail.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_superuser_can_access_pending_detail(self):
        """スーパーユーザーはpendingの詳細にもアクセスできる"""
        self.client.login(username='admin_test', password='testpass123')
        url = reverse('event:detail', kwargs={'pk': self.pending_detail.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_community_manager_can_access_pending_detail(self):
        """コミュニティ管理者はpendingの詳細にアクセスできる"""
        self.client.login(username='community_mgr', password='testpass123')
        url = reverse('event:detail', kwargs={'pk': self.pending_detail.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_community_manager_can_access_rejected_detail(self):
        """コミュニティ管理者はrejectedの詳細にアクセスできる"""
        self.client.login(username='community_mgr', password='testpass123')
        url = reverse('event:detail', kwargs={'pk': self.rejected_detail.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_normal_user_cannot_access_pending_detail(self):
        """一般ユーザーはpendingの詳細にアクセスすると404"""
        self.client.login(username='normal_user', password='testpass123')
        url = reverse('event:detail', kwargs={'pk': self.pending_detail.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_normal_user_cannot_access_rejected_detail(self):
        """一般ユーザーはrejectedの詳細にアクセスすると404"""
        self.client.login(username='normal_user', password='testpass123')
        url = reverse('event:detail', kwargs={'pk': self.rejected_detail.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_applicant_can_access_own_pending_detail(self):
        """申請者本人は自分のpendingの詳細にアクセスできる"""
        self.client.login(username='applicant_user', password='testpass123')
        url = reverse('event:detail', kwargs={'pk': self.pending_detail.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_applicant_can_access_own_rejected_detail(self):
        """申請者本人は自分のrejectedの詳細にアクセスできる"""
        self.rejected_detail.applicant = self.applicant_user
        self.rejected_detail.save()
        self.client.login(username='applicant_user', password='testpass123')
        url = reverse('event:detail', kwargs={'pk': self.rejected_detail.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


class EventDetailPastListFilterTest(TestCase):
    """EventDetailPastList でapprovedのみ表示されるテスト"""

    def setUp(self):
        self.client = Client()

        self.community = Community.objects.create(
            name='Past List Test Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
        )

        self.event = Event.objects.create(
            community=self.community,
            date=date.today() - timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
        )

        self.approved_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Approved Past LT',
            speaker='Approved Past Speaker',
            status='approved',
            duration=15,
            start_time=time(22, 0),
        )

        self.rejected_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Rejected Past LT',
            speaker='Rejected Past Speaker',
            status='rejected',
            rejection_reason='Not suitable',
            duration=15,
            start_time=time(22, 15),
        )

        self.pending_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Pending Past LT',
            speaker='Pending Past Speaker',
            status='pending',
            duration=15,
            start_time=time(22, 30),
        )

    def test_past_list_only_shows_approved(self):
        """LT履歴一覧で承認済みのみ表示される"""
        url = reverse('event:detail_history')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        event_details = response.context['event_details']
        detail_ids = [d.id for d in event_details]

        self.assertIn(self.approved_detail.id, detail_ids)
        self.assertNotIn(self.rejected_detail.id, detail_ids)
        self.assertNotIn(self.pending_detail.id, detail_ids)


class EventLogListViewFilterTest(TestCase):
    """EventLogListView でapprovedのみ表示されるテスト"""

    def setUp(self):
        self.client = Client()

        self.community = Community.objects.create(
            name='Event Log Test Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
        )

        self.event = Event.objects.create(
            community=self.community,
            date=date.today() - timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
        )

        self.approved_special = EventDetail.objects.create(
            event=self.event,
            detail_type='SPECIAL',
            theme='Approved Special Log',
            status='approved',
            duration=60,
            start_time=time(22, 0),
        )

        self.rejected_blog = EventDetail.objects.create(
            event=self.event,
            detail_type='BLOG',
            theme='Rejected Blog Log',
            status='rejected',
            rejection_reason='Not suitable',
            duration=60,
            start_time=time(22, 0),
        )

        self.pending_special = EventDetail.objects.create(
            event=self.event,
            detail_type='SPECIAL',
            theme='Pending Special Log',
            status='pending',
            duration=60,
            start_time=time(22, 0),
        )

    def test_event_log_list_only_shows_approved(self):
        """特別企画/ブログ一覧で承認済みのみ表示される"""
        url = reverse('event:event_log_list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        event_logs = response.context['event_logs']
        log_ids = [d.id for d in event_logs]

        self.assertIn(self.approved_special.id, log_ids)
        self.assertNotIn(self.rejected_blog.id, log_ids)
        self.assertNotIn(self.pending_special.id, log_ids)


class SitemapFilterTest(TestCase):
    """サイトマップでapprovedのEventDetailのみ含まれるテスト"""

    def setUp(self):
        self.client = Client()

        self.community = Community.objects.create(
            name='Sitemap Test Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
        )

        self.event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
        )

        self.approved_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Approved Sitemap LT',
            speaker='Speaker A',
            status='approved',
            duration=15,
            start_time=time(22, 0),
        )

        self.rejected_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Rejected Sitemap LT',
            speaker='Speaker B',
            status='rejected',
            rejection_reason='Not suitable',
            duration=15,
            start_time=time(22, 15),
        )

        self.pending_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Pending Sitemap LT',
            speaker='Speaker C',
            status='pending',
            duration=15,
            start_time=time(22, 30),
        )

    def test_sitemap_only_includes_approved_details(self):
        """サイトマップにapprovedのEventDetailのみ含まれる"""
        url = reverse('sitemap:sitemap')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        event_details = response.context['event_details']
        detail_ids = [d.id for d in event_details]

        self.assertIn(self.approved_detail.id, detail_ids)
        self.assertNotIn(self.rejected_detail.id, detail_ids)
        self.assertNotIn(self.pending_detail.id, detail_ids)


class RelatedEventDetailsFilterTest(TestCase):
    """EventDetailView._fetch_related_event_details で承認済みのみ返るテスト"""

    def setUp(self):
        self.client = Client()

        self.community = Community.objects.create(
            name='Related Details Test Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
        )

        self.event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
        )

        self.approved_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Main LT',
            speaker='Main Speaker',
            status='approved',
            duration=15,
            start_time=time(22, 0),
            h1='Main Article Title',
        )

        self.approved_related = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Related Approved LT',
            speaker='Related Approved Speaker',
            status='approved',
            duration=15,
            start_time=time(22, 15),
            h1='Approved Related Title',
        )

        self.rejected_related = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Related Rejected LT',
            speaker='Related Rejected Speaker',
            status='rejected',
            rejection_reason='Not suitable',
            duration=15,
            start_time=time(22, 30),
            h1='Rejected Related Title',
        )

    def test_related_details_only_includes_approved(self):
        """関連記事に承認済みのEventDetailのみ含まれる"""
        from django.core.cache import cache
        cache.clear()

        url = reverse('event:detail', kwargs={'pk': self.approved_detail.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        related = response.context.get('related_event_details', [])
        related_h1s = [r['h1'] for r in related]

        self.assertIn('Approved Related Title', related_h1s)
        self.assertNotIn('Rejected Related Title', related_h1s)
