"""EventDetailFormのテスト"""
from datetime import date, time, timedelta

from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model

from community.models import Community
from event.forms import EventDetailForm
from event.models import Event, EventDetail

User = get_user_model()


class EventDetailFormCleanTest(TestCase):
    """EventDetailForm.clean()のテスト"""

    def setUp(self):
        """テスト用データの準備"""
        self.factory = RequestFactory()

        # ユーザー作成
        self.user = User.objects.create_user(
            user_name='TestUser',
            email='test@example.com',
            password='testpass123'
        )

        # 集会作成
        self.community = Community.objects.create(
            name='Test Community',
            custom_user=self.user,
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved'
        )

        # イベント作成
        self.event = Event.objects.create(
            community=self.community,
            date=date.today() + timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon'
        )

        # 既存のEventDetail（更新テスト用）
        self.existing_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            theme='Existing Theme',
            speaker='Existing Speaker',
            start_time=time(22, 0),
            duration=30
        )

    def _create_request(self):
        """テスト用のリクエストオブジェクトを作成"""
        request = self.factory.get('/')
        request.user = self.user
        return request

    def test_blog_type_copies_h1_to_theme(self):
        """BLOGタイプでh1が設定されている場合、themeにh1がコピーされる"""
        request = self._create_request()
        form_data = {
            'detail_type': 'BLOG',
            'h1': 'My Blog Title',
            'theme': '',  # 空欄でも h1 がコピーされる
            'speaker': '',
            'start_time': '22:00',
            'duration': 30,
            'contents': 'Blog content here',
            'generate_blog_article': False,
        }

        form = EventDetailForm(data=form_data, request=request, instance=self.existing_detail)
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")

        cleaned_data = form.cleaned_data
        self.assertEqual(cleaned_data['theme'], 'My Blog Title')

    def test_blog_type_with_empty_h1_sets_default_theme(self):
        """BLOGタイプでh1が空の場合、themeが「Blog」になる"""
        request = self._create_request()
        form_data = {
            'detail_type': 'BLOG',
            'h1': '',  # 空
            'theme': '',
            'speaker': '',
            'start_time': '22:00',
            'duration': 30,
            'contents': 'Blog content here',
            'generate_blog_article': False,
        }

        form = EventDetailForm(data=form_data, request=request, instance=self.existing_detail)
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")

        cleaned_data = form.cleaned_data
        self.assertEqual(cleaned_data['theme'], 'Blog')

    def test_blog_type_sets_default_speaker(self):
        """BLOGタイプではspeakerが空文字に設定される"""
        request = self._create_request()
        form_data = {
            'detail_type': 'BLOG',
            'h1': 'Test Title',
            'theme': '',
            'speaker': 'Some Speaker',  # 入力があっても上書きされる
            'start_time': '22:00',
            'duration': 30,
            'contents': 'Blog content here',
            'generate_blog_article': False,
        }

        form = EventDetailForm(data=form_data, request=request, instance=self.existing_detail)
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")

        cleaned_data = form.cleaned_data
        self.assertEqual(cleaned_data['speaker'], '')

    def test_blog_type_sets_default_duration(self):
        """BLOGタイプではdurationが30に設定される"""
        request = self._create_request()
        form_data = {
            'detail_type': 'BLOG',
            'h1': 'Test Title',
            'theme': '',
            'speaker': '',
            'start_time': '22:00',
            'duration': 60,  # 入力があっても上書きされる
            'contents': 'Blog content here',
            'generate_blog_article': False,
        }

        form = EventDetailForm(data=form_data, request=request, instance=self.existing_detail)
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")

        cleaned_data = form.cleaned_data
        self.assertEqual(cleaned_data['duration'], 30)

    def test_special_type_sets_default_theme(self):
        """SPECIALタイプではthemeが「Special Event」になる"""
        request = self._create_request()
        form_data = {
            'detail_type': 'SPECIAL',
            'h1': '',
            'theme': '',
            'speaker': '',
            'start_time': '22:00',
            'duration': 30,
            'contents': 'Special event content',
            'generate_blog_article': False,
        }

        form = EventDetailForm(data=form_data, request=request, instance=self.existing_detail)
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")

        cleaned_data = form.cleaned_data
        self.assertEqual(cleaned_data['theme'], 'Special Event')

    def test_special_type_sets_default_duration(self):
        """SPECIALタイプではdurationが60に設定される"""
        request = self._create_request()
        form_data = {
            'detail_type': 'SPECIAL',
            'h1': '',
            'theme': '',
            'speaker': '',
            'start_time': '22:00',
            'duration': 30,  # 入力があっても上書きされる
            'contents': 'Special event content',
            'generate_blog_article': False,
        }

        form = EventDetailForm(data=form_data, request=request, instance=self.existing_detail)
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")

        cleaned_data = form.cleaned_data
        self.assertEqual(cleaned_data['duration'], 60)

    def test_lt_type_preserves_user_input(self):
        """LTタイプではユーザー入力が保持される"""
        request = self._create_request()
        form_data = {
            'detail_type': 'LT',
            'h1': '',
            'theme': 'User Entered Theme',
            'speaker': 'User Entered Speaker',
            'start_time': '22:30',
            'duration': 45,
            'contents': 'LT content',
            'generate_blog_article': False,
        }

        form = EventDetailForm(data=form_data, request=request, instance=self.existing_detail)
        self.assertTrue(form.is_valid(), msg=f"Form errors: {form.errors}")

        cleaned_data = form.cleaned_data
        self.assertEqual(cleaned_data['theme'], 'User Entered Theme')
        self.assertEqual(cleaned_data['speaker'], 'User Entered Speaker')
        self.assertEqual(cleaned_data['duration'], 45)
