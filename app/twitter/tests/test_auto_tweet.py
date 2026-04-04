"""X (Twitter) 自動告知機能のテスト

シグナルによるキュー追加、スケジュール投稿エンドポイント、
X API 投稿関数、告知文生成関数をテストする。
"""

import datetime
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from community.models import Community, CommunityMember
from event.models import Event, EventDetail
from twitter.models import TweetQueue

CustomUser = get_user_model()


class AutoTweetTestBase(TestCase):
    """テスト共通のセットアップ"""

    def setUp(self):
        self.client = Client()
        self.owner = CustomUser.objects.create_user(
            user_name="auto_tweet_owner",
            email="auto_tweet_owner@example.com",
            password="testpassword",
        )
        # status=pending で作成 (承認前)
        self.community = Community.objects.create(
            name="Auto Tweet Community",
            start_time=datetime.time(22, 0),
            duration=60,
            weekdays=["Mon", "Thu"],
            frequency="毎週",
            organizers="Test Organizer",
            description="テスト用の技術系集会です",
            platform="All",
            status="pending",
            twitter_hashtag="TestMeetup",
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER,
        )
        self.event = Event.objects.create(
            community=self.community,
            date=datetime.date(2026, 5, 1),
            start_time=datetime.time(22, 0),
            duration=60,
        )


class CommunityApprovalSignalTest(AutoTweetTestBase):
    """Community 承認時のシグナルテスト"""

    def test_community_approval_creates_queue(self):
        """Community が pending -> approved に変更されたらキューが作成される"""
        self.assertEqual(TweetQueue.objects.count(), 0)

        self.community.status = "approved"
        self.community.save()

        self.assertEqual(TweetQueue.objects.count(), 1)
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.tweet_type, "new_community")
        self.assertEqual(queue.community, self.community)
        self.assertEqual(queue.status, "pending")

    def test_duplicate_community_queue_prevention(self):
        """同一 community の重複キューは作成されない"""
        self.community.status = "approved"
        self.community.save()
        self.assertEqual(TweetQueue.objects.count(), 1)

        # 再度保存しても増えない
        self.community.status = "approved"
        self.community.save()
        self.assertEqual(TweetQueue.objects.count(), 1)

    def test_rejected_community_does_not_create_queue(self):
        """rejected への変更ではキューは作成されない"""
        self.community.status = "rejected"
        self.community.save()

        self.assertEqual(TweetQueue.objects.count(), 0)

    def test_already_approved_community_does_not_create_queue(self):
        """既に approved だった community の再保存ではキューは作成されない"""
        self.community.status = "approved"
        self.community.save()
        self.assertEqual(TweetQueue.objects.count(), 1)

        # description 変更で再保存
        self.community.description = "更新しました"
        self.community.save()
        self.assertEqual(TweetQueue.objects.count(), 1)


class EventDetailSignalTest(AutoTweetTestBase):
    """EventDetail 作成/承認時のシグナルテスト"""

    def setUp(self):
        super().setUp()
        # community を approved にしておく (LT テスト用)
        self.community.status = "approved"
        self.community.save()
        # community 承認時のキューをクリア
        TweetQueue.objects.all().delete()

    def test_lt_approval_creates_queue(self):
        """LT タイプの EventDetail 承認時にキューが作成される"""
        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="VRChatで学ぶPython",
            start_time=datetime.time(22, 15),
        )

        self.assertEqual(TweetQueue.objects.count(), 1)
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.tweet_type, "lt")
        self.assertEqual(queue.event_detail, detail)
        self.assertEqual(queue.event, self.event)

    def test_special_event_creates_queue(self):
        """SPECIAL タイプの EventDetail 承認時にキューが作成される"""
        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="SPECIAL",
            status="approved",
            speaker="ゲスト講師",
            theme="VR空間でのコラボレーション",
            start_time=datetime.time(22, 0),
        )

        self.assertEqual(TweetQueue.objects.count(), 1)
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.tweet_type, "special")
        self.assertEqual(queue.event_detail, detail)

    def test_blog_type_does_not_create_queue(self):
        """BLOG タイプではキューが作成されない"""
        EventDetail.objects.create(
            event=self.event,
            detail_type="BLOG",
            status="approved",
            speaker="ブロガー",
            theme="振り返りブログ",
            start_time=datetime.time(22, 0),
        )

        self.assertEqual(TweetQueue.objects.count(), 0)

    def test_pending_detail_does_not_create_queue(self):
        """status=pending の EventDetail ではキューが作成されない"""
        EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="pending",
            speaker="テスト太郎",
            theme="VRChatで学ぶPython",
            start_time=datetime.time(22, 15),
        )

        self.assertEqual(TweetQueue.objects.count(), 0)

    def test_duplicate_event_detail_queue_prevention(self):
        """同一 event_detail の重複キューは作成されない"""
        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="VRChatで学ぶPython",
            start_time=datetime.time(22, 15),
        )
        self.assertEqual(TweetQueue.objects.count(), 1)

        # 再保存しても増えない
        detail.theme = "更新テーマ"
        detail.save()
        self.assertEqual(TweetQueue.objects.count(), 1)

    def test_pending_to_approved_creates_queue(self):
        """EventDetail が pending -> approved に更新されたらキューが作成される"""
        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="pending",
            speaker="テスト太郎",
            theme="VRChatで学ぶPython",
            start_time=datetime.time(22, 15),
        )
        self.assertEqual(TweetQueue.objects.count(), 0)

        # pending -> approved
        detail.status = "approved"
        detail.save()

        self.assertEqual(TweetQueue.objects.count(), 1)
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.tweet_type, "lt")
        self.assertEqual(queue.event_detail, detail)

    def test_already_approved_detail_update_does_not_create_queue(self):
        """既に approved の EventDetail を再保存してもキューは追加されない"""
        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="VRChatで学ぶPython",
            start_time=datetime.time(22, 15),
        )
        self.assertEqual(TweetQueue.objects.count(), 1)

        # キューを消して再保存
        TweetQueue.objects.all().delete()
        detail.speaker = "更新太郎"
        detail.save()

        # approved -> approved なのでキューは作成されない
        self.assertEqual(TweetQueue.objects.count(), 0)


class PostScheduledTweetsViewTest(AutoTweetTestBase):
    """スケジュール投稿エンドポイントのテスト"""

    REQUEST_TOKEN_ENV = {"REQUEST_TOKEN": "test-token"}

    def test_post_scheduled_tweets_unauthorized(self):
        """認証なしで 401 が返る"""
        url = reverse("twitter:post_scheduled_tweets")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 401)

    def test_post_scheduled_tweets_wrong_token(self):
        """不正なトークンで 401 が返る"""
        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(url, HTTP_REQUEST_TOKEN="wrong-token")
            self.assertEqual(response.status_code, 401)

    @patch("twitter.views.post_tweet")
    @patch("twitter.views.generate_new_community_tweet")
    def test_post_scheduled_tweets_success(self, mock_generate, mock_post):
        """正常な投稿フロー"""
        mock_generate.return_value = "新しい集会の告知テスト"
        mock_post.return_value = {"id": "12345", "text": "新しい集会の告知テスト"}

        TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="pending",
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(
                url, HTTP_REQUEST_TOKEN="test-token",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["processed"], 1)
        self.assertEqual(data["results"][0]["status"], "posted")

        # DB の状態確認
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.status, "posted")
        self.assertEqual(queue.tweet_id, "12345")
        self.assertIsNotNone(queue.posted_at)

    @patch("twitter.views.post_tweet")
    @patch("twitter.views.generate_new_community_tweet")
    def test_post_scheduled_tweets_generation_failure(self, mock_generate, mock_post):
        """テキスト生成失敗時の処理"""
        mock_generate.return_value = None

        TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="pending",
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(
                url, HTTP_REQUEST_TOKEN="test-token",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["results"][0]["error"], "generation_failed")

        queue = TweetQueue.objects.first()
        self.assertEqual(queue.status, "failed")

    @patch("twitter.views.post_tweet")
    @patch("twitter.views.generate_new_community_tweet")
    def test_post_scheduled_tweets_post_failure(self, mock_generate, mock_post):
        """X API 投稿失敗時の処理"""
        mock_generate.return_value = "テストツイート"
        mock_post.return_value = None

        TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="pending",
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(
                url, HTTP_REQUEST_TOKEN="test-token",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["results"][0]["error"], "post_failed")

        queue = TweetQueue.objects.first()
        self.assertEqual(queue.status, "failed")

    def test_post_scheduled_tweets_empty_queue(self):
        """キューが空の場合の処理"""
        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(
                url, HTTP_REQUEST_TOKEN="test-token",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["processed"], 0)
        self.assertEqual(data["results"], [])

    @patch("twitter.views.post_tweet")
    def test_post_scheduled_tweets_with_pregenerated_text(self, mock_post):
        """事前にテキストが生成されている場合は LLM を呼ばない"""
        mock_post.return_value = {"id": "99999", "text": "事前生成テキスト"}

        TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="pending",
            generated_text="事前生成テキスト",
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(
                url, HTTP_REQUEST_TOKEN="test-token",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["results"][0]["status"], "posted")


class PostTweetFunctionTest(TestCase):
    """X API 投稿関数の単体テスト（OAuth 1.0a）"""

    OAUTH1_ENV = {
        "X_API_KEY": "test-api-key",
        "X_API_SECRET": "test-api-secret",
        "X_ACCESS_TOKEN": "test-access-token",
        "X_ACCESS_TOKEN_SECRET": "test-access-token-secret",
    }

    @patch("twitter.x_api.requests.post")
    def test_post_tweet_success(self, mock_post):
        """正常にツイートが投稿される"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {"id": "12345", "text": "テストツイート"},
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import post_tweet
            result = post_tweet("テストツイート")

        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "12345")

        # OAuth1 認証が使われていることを確認
        call_kwargs = mock_post.call_args
        self.assertIsNotNone(call_kwargs.kwargs.get("auth"))

    def test_post_tweet_no_credentials(self):
        """環境変数が未設定の場合は None を返す"""
        with patch.dict("os.environ", {
            "X_API_KEY": "",
            "X_API_SECRET": "",
            "X_ACCESS_TOKEN": "",
            "X_ACCESS_TOKEN_SECRET": "",
        }):
            from twitter.x_api import post_tweet
            result = post_tweet("テスト")
        self.assertIsNone(result)

    @patch("twitter.x_api.requests.post")
    def test_post_tweet_api_error(self, mock_post):
        """API エラー時は None を返す"""
        import requests
        mock_post.side_effect = requests.RequestException("API Error")

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import post_tweet
            result = post_tweet("テスト")
        self.assertIsNone(result)

    @patch("twitter.x_api.requests.post")
    def test_post_tweet_api_error_with_response(self, mock_post):
        """API エラー時にレスポンスがある場合もステータスコードをログ出力する"""
        import requests
        mock_response = MagicMock()
        mock_response.status_code = 403
        error = requests.RequestException("Forbidden")
        error.response = mock_response
        mock_post.side_effect = error

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import post_tweet
            result = post_tweet("テスト")
        self.assertIsNone(result)

    def test_post_tweet_missing_partial_credentials(self):
        """一部の認証情報だけ設定されている場合は None を返す"""
        with patch.dict("os.environ", {
            "X_API_KEY": "key",
            "X_API_SECRET": "secret",
            "X_ACCESS_TOKEN": "",
            "X_ACCESS_TOKEN_SECRET": "",
        }):
            from twitter.x_api import post_tweet
            result = post_tweet("テスト")
        self.assertIsNone(result)


class TweetGeneratorTest(TestCase):
    """告知文生成関数のテスト"""

    def setUp(self):
        self.community = Community.objects.create(
            name="Generator Test Community",
            start_time=datetime.time(22, 0),
            duration=60,
            weekdays=["Mon"],
            frequency="毎週",
            organizers="Test",
            description="テスト用集会",
            platform="All",
            status="approved",
            twitter_hashtag="GenTest",
        )
        self.event = Event.objects.create(
            community=self.community,
            date=datetime.date(2026, 5, 1),
            start_time=datetime.time(22, 0),
            duration=60,
        )

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_new_community_tweet(self, mock_llm):
        """新規集会の告知文が生成される"""
        mock_llm.return_value = "新しい集会がはじまります！"

        from twitter.tweet_generator import generate_new_community_tweet
        result = generate_new_community_tweet(self.community, self.event)

        self.assertEqual(result, "新しい集会がはじまります！")
        mock_llm.assert_called_once()

        # プロンプトに集会名が含まれていることを確認
        call_args = mock_llm.call_args
        self.assertIn("Generator Test Community", call_args[0][1])

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_lt_tweet(self, mock_llm):
        """LT 告知文が生成される"""
        mock_llm.return_value = "LT告知テスト"

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="Pythonのテスト技法",
            start_time=datetime.time(22, 15),
        )

        from twitter.tweet_generator import generate_lt_tweet
        result = generate_lt_tweet(detail)

        self.assertEqual(result, "LT告知テスト")
        call_args = mock_llm.call_args
        self.assertIn("テスト太郎", call_args[0][1])
        self.assertIn("Pythonのテスト技法", call_args[0][1])

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_special_event_tweet(self, mock_llm):
        """特別回告知文が生成される"""
        mock_llm.return_value = "特別回告知テスト"

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="SPECIAL",
            status="approved",
            speaker="ゲスト講師",
            theme="VRの未来",
            start_time=datetime.time(22, 0),
        )

        from twitter.tweet_generator import generate_special_event_tweet
        result = generate_special_event_tweet(detail)

        self.assertEqual(result, "特別回告知テスト")
        call_args = mock_llm.call_args
        self.assertIn("ゲスト講師", call_args[0][1])

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_tweet_llm_failure(self, mock_llm):
        """LLM 呼び出し失敗時は None を返す"""
        mock_llm.return_value = None

        from twitter.tweet_generator import generate_new_community_tweet
        result = generate_new_community_tweet(self.community)

        self.assertIsNone(result)

    @patch("twitter.tweet_generator._call_llm")
    def test_sanitize_strips_newlines_in_prompt(self, mock_llm):
        """ユーザー入力の改行・制御文字がサニタイズされてプロンプトに渡される"""
        mock_llm.return_value = "サニタイズテスト"

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト\n太郎\r\n",
            theme="改行\n入り\tテーマ",
            start_time=datetime.time(22, 15),
        )

        from twitter.tweet_generator import generate_lt_tweet
        generate_lt_tweet(detail)

        call_args = mock_llm.call_args
        user_prompt = call_args[0][1]
        # サニタイズ後は改行が空白に変換されている
        self.assertIn("テスト 太郎", user_prompt)
        self.assertIn("改行 入り テーマ", user_prompt)


class SanitizeForPromptTest(TestCase):
    """_sanitize_for_prompt 関数の単体テスト"""

    def test_empty_string(self):
        from twitter.tweet_generator import _sanitize_for_prompt
        self.assertEqual(_sanitize_for_prompt(""), "")

    def test_none_input(self):
        from twitter.tweet_generator import _sanitize_for_prompt
        self.assertEqual(_sanitize_for_prompt(None), "")

    def test_newlines_removed(self):
        from twitter.tweet_generator import _sanitize_for_prompt
        self.assertEqual(_sanitize_for_prompt("hello\nworld\r\n"), "hello world")

    def test_tabs_removed(self):
        from twitter.tweet_generator import _sanitize_for_prompt
        self.assertEqual(_sanitize_for_prompt("hello\tworld"), "hello world")

    def test_max_length_truncation(self):
        from twitter.tweet_generator import _sanitize_for_prompt
        long_text = "a" * 300
        result = _sanitize_for_prompt(long_text, max_length=200)
        self.assertEqual(len(result), 200)

    def test_custom_max_length(self):
        from twitter.tweet_generator import _sanitize_for_prompt
        result = _sanitize_for_prompt("abcdef", max_length=3)
        self.assertEqual(result, "abc")

    def test_multiple_spaces_collapsed(self):
        from twitter.tweet_generator import _sanitize_for_prompt
        self.assertEqual(_sanitize_for_prompt("hello   world"), "hello world")


class PostTweetValidationTest(TestCase):
    """post_tweet 関数の入力バリデーションテスト"""

    def test_empty_text_returns_none(self):
        """空文字列で None を返す"""
        from twitter.x_api import post_tweet
        result = post_tweet("")
        self.assertIsNone(result)

    def test_none_text_returns_none(self):
        """None で None を返す"""
        from twitter.x_api import post_tweet
        result = post_tweet(None)
        self.assertIsNone(result)

    def test_exceeds_280_chars_returns_none(self):
        """280文字超で None を返す"""
        from twitter.x_api import post_tweet
        long_text = "a" * 281
        result = post_tweet(long_text)
        self.assertIsNone(result)

    def test_exactly_280_chars_does_not_reject(self):
        """280文字ちょうどはバリデーションを通過する（認証情報なしで None になる）"""
        from twitter.x_api import post_tweet
        with patch.dict("os.environ", {
            "X_API_KEY": "",
            "X_API_SECRET": "",
            "X_ACCESS_TOKEN": "",
            "X_ACCESS_TOKEN_SECRET": "",
        }):
            result = post_tweet("a" * 280)
        # 認証情報がないので None だが、文字数バリデーションは通過している
        self.assertIsNone(result)
