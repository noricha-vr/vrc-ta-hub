"""regenerate_ready_tweets 管理コマンドのテスト

ready キューに残っている旧プロンプト生成分（本文4行以上）を
新プロンプトで再生成する機能を検証する。実 LLM は呼ばずに
get_generator の戻り値をモック差し替えする。
"""
import datetime
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from community.models import Community
from twitter.models import TweetQueue


class RegenerateReadyTweetsCommandTest(TestCase):
    """regenerate_ready_tweets コマンドのテスト"""

    def setUp(self):
        # status="pending" でシグナルによる TweetQueue 自動生成を回避
        self.community = Community.objects.create(
            name="Regen Test Community",
            start_time=datetime.time(21, 0),
            duration=60,
            weekdays=["Mon"],
            frequency="Weekly",
            organizers="Test Organizer",
            description="Test Description",
            platform="All",
            status="pending",
        )

    def _create_queue(self, generated_text, status="ready", tweet_type="new_community"):
        return TweetQueue.objects.create(
            tweet_type=tweet_type,
            community=self.community,
            status=status,
            generated_text=generated_text,
        )

    def _call(self, *args):
        out = StringIO()
        call_command("regenerate_ready_tweets", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_does_not_update_db(self):
        """--dry-run では DB が更新されない"""
        four_lines = "行1\n行2\n行3\n行4\nhttps://example.com"
        item = self._create_queue(four_lines)

        with patch("twitter.management.commands.regenerate_ready_tweets.get_generator") as mock_get:
            mock_get.return_value = lambda qi: "新行1\n新行2\n新行3\nhttps://example.com"
            output = self._call("--dry-run")

        item.refresh_from_db()
        self.assertEqual(item.generated_text, four_lines)
        self.assertIn("dry-run skip", output)

    def test_only_over_limit_queues_are_regenerated_by_default(self):
        """デフォルトでは本文4行以上のキューのみ再生成される"""
        over_limit_text = "行1\n行2\n行3\n行4\nhttps://example.com"
        within_limit_text = "行1\n行2\nhttps://example.com"
        over = self._create_queue(over_limit_text)
        within = self._create_queue(within_limit_text)

        new_text = "新A\n新B\nhttps://example.com"
        with patch("twitter.management.commands.regenerate_ready_tweets.get_generator") as mock_get:
            mock_get.return_value = lambda qi: new_text
            self._call()

        over.refresh_from_db()
        within.refresh_from_db()
        self.assertEqual(over.generated_text, new_text)
        self.assertEqual(within.generated_text, within_limit_text)

    def test_all_flag_regenerates_even_within_limit_queues(self):
        """--all で3行以内も再生成対象になる"""
        within_limit_text = "行1\n行2\nhttps://example.com"
        item = self._create_queue(within_limit_text)

        new_text = "新A\n新B\nhttps://example.com"
        with patch("twitter.management.commands.regenerate_ready_tweets.get_generator") as mock_get:
            mock_get.return_value = lambda qi: new_text
            self._call("--all")

        item.refresh_from_db()
        self.assertEqual(item.generated_text, new_text)

    def test_pk_option_filters_to_single_queue(self):
        """--pk 指定時は該当キューのみ対象になる"""
        over_limit = "行1\n行2\n行3\n行4\nhttps://example.com"
        target = self._create_queue(over_limit)
        other = self._create_queue(over_limit)

        new_text = "新A\n新B\nhttps://example.com"
        with patch("twitter.management.commands.regenerate_ready_tweets.get_generator") as mock_get:
            mock_get.return_value = lambda qi: new_text
            self._call("--pk", str(target.pk))

        target.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(target.generated_text, new_text)
        self.assertEqual(other.generated_text, over_limit)

    def test_tweet_type_option_filters_by_type(self):
        """--tweet-type で tweet_type 絞り込みができる"""
        over_limit = "行1\n行2\n行3\n行4\nhttps://example.com"
        lt_item = self._create_queue(over_limit, tweet_type="lt")
        other_item = self._create_queue(over_limit, tweet_type="new_community")

        new_text = "新A\n新B\nhttps://example.com"
        with patch("twitter.management.commands.regenerate_ready_tweets.get_generator") as mock_get:
            mock_get.return_value = lambda qi: new_text
            self._call("--tweet-type", "lt")

        lt_item.refresh_from_db()
        other_item.refresh_from_db()
        self.assertEqual(lt_item.generated_text, new_text)
        self.assertEqual(other_item.generated_text, over_limit)

    def test_generator_returning_none_does_not_update_and_counts_failure(self):
        """生成関数が None を返した場合、generated_text が更新されず失敗に計上される"""
        over_limit = "行1\n行2\n行3\n行4\nhttps://example.com"
        item = self._create_queue(over_limit)

        with patch("twitter.management.commands.regenerate_ready_tweets.get_generator") as mock_get:
            mock_get.return_value = lambda qi: None
            output = self._call()

        item.refresh_from_db()
        self.assertEqual(item.generated_text, over_limit)
        self.assertIn("失敗=1", output)

    def test_unknown_tweet_type_is_skipped(self):
        """get_generator が None を返した場合はスキップし失敗扱いになる"""
        over_limit = "行1\n行2\n行3\n行4\nhttps://example.com"
        item = self._create_queue(over_limit)

        with patch("twitter.management.commands.regenerate_ready_tweets.get_generator") as mock_get:
            mock_get.return_value = None
            output = self._call()

        item.refresh_from_db()
        self.assertEqual(item.generated_text, over_limit)
        self.assertIn("失敗=1", output)

    def test_non_ready_queues_are_ignored(self):
        """ready 以外の status は対象外"""
        over_limit = "行1\n行2\n行3\n行4\nhttps://example.com"
        posted_item = self._create_queue(over_limit, status="posted")

        new_text = "新A\n新B\nhttps://example.com"
        with patch("twitter.management.commands.regenerate_ready_tweets.get_generator") as mock_get:
            mock_get.return_value = lambda qi: new_text
            self._call()

        posted_item.refresh_from_db()
        self.assertEqual(posted_item.generated_text, over_limit)

    def test_status_remains_ready_after_regeneration(self):
        """再生成後も status は ready のまま"""
        over_limit = "行1\n行2\n行3\n行4\nhttps://example.com"
        item = self._create_queue(over_limit)

        with patch("twitter.management.commands.regenerate_ready_tweets.get_generator") as mock_get:
            mock_get.return_value = lambda qi: "新A\n新B\nhttps://example.com"
            self._call()

        item.refresh_from_db()
        self.assertEqual(item.status, "ready")
