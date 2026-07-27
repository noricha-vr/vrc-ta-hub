"""x_api のエラーログ縮小テスト（Issue #538）

X API のエラー body 全量（1000B）をログに出さず、先頭 200B + errorCode に絞る。
"""
from django.test import SimpleTestCase

from twitter.x_api import (
    ERROR_BODY_LOG_MAX_BYTES,
    _extract_error_code,
    _summarize_error_body,
    _truncate_utf8,
)


class ExtractErrorCodeTest(SimpleTestCase):
    def test_extracts_v1_style_errors_code(self):
        body = '{"errors": [{"code": 187, "message": "Status is a duplicate."}]}'
        self.assertEqual(_extract_error_code(body), '187')

    def test_extracts_v2_style_type(self):
        body = '{"title": "Forbidden", "type": "about:blank", "status": 403}'
        self.assertEqual(_extract_error_code(body), 'about:blank')

    def test_returns_none_for_non_json(self):
        self.assertIsNone(_extract_error_code('<html>Bad Gateway</html>'))

    def test_returns_none_for_empty_body(self):
        self.assertIsNone(_extract_error_code(''))
        self.assertIsNone(_extract_error_code(None))


class TruncateUtf8Test(SimpleTestCase):
    def test_keeps_short_text_as_is(self):
        self.assertEqual(_truncate_utf8('あいう', ERROR_BODY_LOG_MAX_BYTES), 'あいう')

    def test_truncates_multibyte_by_bytes(self):
        # 日本語は 1 文字 3 バイト。200B なら 66 文字（198B）まで。
        text = 'あ' * 500
        truncated = _truncate_utf8(text, ERROR_BODY_LOG_MAX_BYTES)

        self.assertLessEqual(len(truncated.encode('utf-8')), ERROR_BODY_LOG_MAX_BYTES)
        self.assertEqual(truncated, 'あ' * 66)

    def test_does_not_emit_broken_multibyte_sequence(self):
        """文字境界で切るため、切り詰め結果は必ず decode 可能"""
        text = '絵文字😀' * 100  # 4 バイト文字を含む
        truncated = _truncate_utf8(text, ERROR_BODY_LOG_MAX_BYTES)

        self.assertLessEqual(len(truncated.encode('utf-8')), ERROR_BODY_LOG_MAX_BYTES)
        self.assertTrue(text.startswith(truncated))


class SummarizeErrorBodyTest(SimpleTestCase):
    def test_truncates_to_200_bytes(self):
        body = 'x' * 1000
        summary = _summarize_error_body(body)

        self.assertEqual(summary.count('x'), ERROR_BODY_LOG_MAX_BYTES)
        self.assertNotIn('x' * (ERROR_BODY_LOG_MAX_BYTES + 1), summary)

    def test_multibyte_body_snippet_stays_within_200_bytes(self):
        """日本語 body でも本文部分が 200 バイトを超えない"""
        body = 'エラー詳細' * 200
        summary = _summarize_error_body(body)

        snippet = summary.split(']=', 1)[1]
        self.assertLessEqual(len(snippet.encode('utf-8')), ERROR_BODY_LOG_MAX_BYTES)

    def test_includes_error_code_when_available(self):
        body = '{"errors": [{"code": 187, "message": "Status is a duplicate."}]}'
        summary = _summarize_error_body(body)

        self.assertIn('errorCode=187', summary)

    def test_handles_none_body(self):
        self.assertIn('body[:', _summarize_error_body(None))
