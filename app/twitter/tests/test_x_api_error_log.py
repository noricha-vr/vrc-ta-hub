"""x_api のエラーログ縮小テスト（Issue #538）

X API のエラー body 全量（1000B）をログに出さず、先頭 200B + errorCode に絞る。
"""
from django.test import SimpleTestCase

from twitter.x_api import (
    ERROR_BODY_LOG_MAX_LENGTH,
    _extract_error_code,
    _summarize_error_body,
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


class SummarizeErrorBodyTest(SimpleTestCase):
    def test_truncates_to_200_bytes(self):
        body = 'x' * 1000
        summary = _summarize_error_body(body)

        self.assertEqual(summary.count('x'), ERROR_BODY_LOG_MAX_LENGTH)
        self.assertNotIn('x' * (ERROR_BODY_LOG_MAX_LENGTH + 1), summary)

    def test_includes_error_code_when_available(self):
        body = '{"errors": [{"code": 187, "message": "Status is a duplicate."}]}'
        summary = _summarize_error_body(body)

        self.assertIn('errorCode=187', summary)

    def test_handles_none_body(self):
        self.assertIn('body[:', _summarize_error_body(None))
