"""Event app test package."""

from unittest.mock import patch


# Event tests do not verify tweet text generation itself. Keep TweetQueue creation
# side effects, but prevent background generator threads from racing SQLite tests.
_tweet_generation_patcher = patch('twitter.signals._start_tweet_generation')
_tweet_generation_patcher.start()
