"""プロンプト入力サニタイズのテスト。"""


from django.test import TestCase, tag


@tag('offline_external_api')
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
