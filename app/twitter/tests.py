from datetime import datetime, time
from unittest.mock import patch, MagicMock

from django.test import TestCase

from event.models import Event, EventDetail
from twitter.models import TwitterTemplate, Community
from twitter.utils import get_tweet_template, format_event_info, generate_tweet, generate_tweet_url


class TwitterUtilsTestCase(TestCase):
    def setUp(self):
        self.community = Community.objects.create(name="Test Community")
        self.event = Event.objects.create(
            community=self.community,
            date=datetime(2023, 5, 1).date(),
            start_time=time(14, 0)
        )
        EventDetail.objects.create(
            event=self.event,
            start_time=time(14, 0),
            theme="Test Theme 1",
            speaker="Test Speaker 1"
        )

    def test_get_tweet_template(self):
        # テンプレートが存在しない場合のテスト
        template = get_tweet_template(self.community)
        self.assertEqual(template, "{event_name}が{date}に開催されます！ {details}")

        # テンプレートが存在する場合のテスト
        TwitterTemplate.objects.create(
            community=self.community,
            template="Custom template: {event_name} on {date}"
        )
        template = get_tweet_template(self.community)
        self.assertEqual(template, "Custom template: {event_name} on {date}")

    def test_format_event_info(self):
        event_info = format_event_info(self.event)
        expected_info = {
            "event_name": "Test Community",
            "date": "2023年05月01日",
            "time": "14:00",
            "details": "14:00 - Test Theme 1 (Test Speaker 1)"
        }
        self.assertEqual(event_info, expected_info)

    @patch('twitter.utils.openai.ChatCompletion.create')
    def test_generate_tweet(self, mock_openai_create):
        mock_response = MagicMock()
        mock_response.choices[0].message = {'content': 'Generated tweet'}
        mock_openai_create.return_value = mock_response

        tweet = generate_tweet("Template",
                               {"event_name": "Test", "date": "2023-05-01", "time": "14:00", "details": "Details"})

        self.assertEqual(tweet, 'Generated tweet')

    @patch('twitter.utils.generate_tweet')
    def test_generate_tweet_url(self, mock_generate_tweet):
        mock_generate_tweet.return_value = "Test tweet"

        url = generate_tweet_url(self.event)

        expected_url = "https://twitter.com/intent/tweet?text=Test+tweet"
        self.assertEqual(url, expected_url)

    @patch('twitter.utils.generate_tweet')
    def test_generate_tweet_url_error(self, mock_generate_tweet):
        mock_generate_tweet.return_value = None

        url = generate_tweet_url(self.event)

        self.assertIsNone(url)

    def test_format_event_info_multiple_details(self):
        # 追加のイベント詳細を作成
        EventDetail.objects.create(
            event=self.event,
            start_time=time(15, 0),
            theme="Test Theme 2",
            speaker="Test Speaker 2"
        )
        EventDetail.objects.create(
            event=self.event,
            start_time=time(16, 0),
            theme="Test Theme 3",
            speaker="Test Speaker 3"
        )

        event_info = format_event_info(self.event)
        expected_info = {
            "event_name": "Test Community",
            "date": "2023年05月01日",
            "time": "14:00",
            "details": "14:00 - Test Theme 1 (Test Speaker 1)\n15:00 - Test Theme 2 (Test Speaker 2)\n16:00 - Test Theme 3 (Test Speaker 3)"
        }
        self.assertEqual(event_info, expected_info)

    @patch('twitter.utils.openai.ChatCompletion.create')
    def test_generate_tweet_with_multiple_details(self, mock_openai_create):
        # 追加のイベント詳細を作成
        EventDetail.objects.create(
            event=self.event,
            start_time=time(15, 0),
            theme="Test Theme 2",
            speaker="Test Speaker 2"
        )

        mock_response = MagicMock()
        mock_response.choices[0].message = {'content': 'Generated tweet with multiple details'}
        mock_openai_create.return_value = mock_response

        template = get_tweet_template(self.community)
        event_info = format_event_info(self.event)
        tweet = generate_tweet(template, event_info)

        self.assertEqual(tweet, 'Generated tweet with multiple details')
        # OpenAI APIが正しい情報で呼び出されたことを確認
        mock_openai_create.assert_called_once()
        call_args = mock_openai_create.call_args[1]
        self.assertIn("14:00 - Test Theme 1 (Test Speaker 1)", call_args['messages'][1]['content'])
        self.assertIn("15:00 - Test Theme 2 (Test Speaker 2)", call_args['messages'][1]['content'])

    @patch('twitter.utils.generate_tweet')
    def test_generate_tweet_url_with_multiple_details(self, mock_generate_tweet):
        # 追加のイベント詳細を作成
        EventDetail.objects.create(
            event=self.event,
            start_time=time(15, 0),
            theme="Test Theme 2",
            speaker="Test Speaker 2"
        )

        mock_generate_tweet.return_value = "Test tweet with multiple details"

        url = generate_tweet_url(self.event)

        expected_url = "https://twitter.com/intent/tweet?text=Test+tweet+with+multiple+details"
        self.assertEqual(url, expected_url)
        # generate_tweet関数が正しい情報で呼び出されたことを確認
        mock_generate_tweet.assert_called_once()
        call_args = mock_generate_tweet.call_args[0]
        self.assertIn("14:00 - Test Theme 1 (Test Speaker 1)", call_args[1]['details'])
        self.assertIn("15:00 - Test Theme 2 (Test Speaker 2)", call_args[1]['details'])
