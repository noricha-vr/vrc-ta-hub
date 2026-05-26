"""Tweet generation patch helpers for event tests."""

from unittest.mock import patch


_TWEET_GENERATION_TARGET = 'twitter.signals._start_tweet_generation'


class TweetGenerationPatchMixin:
    """Patch tweet generation for one Django TestCase lifecycle."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tweet_generation_patcher = patch(_TWEET_GENERATION_TARGET)
        cls._tweet_generation_patcher.start()

        try:
            super().setUpClass()
        except Exception:
            cls._tweet_generation_patcher.stop()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            super().tearDownClass()
        finally:
            cls._tweet_generation_patcher.stop()
