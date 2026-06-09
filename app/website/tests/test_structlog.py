"""structlog ベース構造化ログのテスト.

- 既存の `logger.info(...)` / `logger.error(...)` シグネチャが無変更で動作するか
- DEBUG=True で人間可読 (ConsoleRenderer) 形式になるか
- DEBUG=False で JSON 形式になるか
- assertLogs と互換 (テスト基盤に影響しないか) を確認する。
"""
from __future__ import annotations

import io
import json
import logging

import structlog
from django.test import SimpleTestCase

from website.settings import base as settings_base


def _build_formatter(*, json_mode: bool) -> logging.Formatter:
    """テスト用に独立した ProcessorFormatter を構築する.

    LOGGING dict 経由で動的に DEBUG を切り替えるとモジュール再読込が必要になるため、
    ここでは settings/base.py と同じ pre_chain を共有しつつ、レンダラーだけ切り替えた
    フォーマッタを直接組み立ててテストする。
    """
    renderer = (
        structlog.processors.JSONRenderer()
        if json_mode
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    return structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=settings_base._foreign_pre_chain,
    )


def _capture(formatter: logging.Formatter, level: int = logging.INFO) -> tuple[logging.Logger, io.StringIO]:
    """テスト専用のロガーとストリームをセットアップする."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    logger = logging.getLogger(f'website.tests.structlog.{id(stream)}')
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
    return logger, stream


class StructlogJsonRenderingTests(SimpleTestCase):
    """DEBUG=False 相当: JSON 出力."""

    def test_info_message_renders_as_json(self):
        logger, stream = _capture(_build_formatter(json_mode=True))
        logger.info('user logged in')

        line = stream.getvalue().strip()
        self.assertTrue(line, 'JSON ログが出力されること')
        payload = json.loads(line)
        self.assertEqual(payload['event'], 'user logged in')
        self.assertEqual(payload['level'], 'info')
        # ISO8601 タイムスタンプが付与されること
        self.assertIn('timestamp', payload)

    def test_error_with_exc_info_includes_exception(self):
        logger, stream = _capture(_build_formatter(json_mode=True), level=logging.DEBUG)
        try:
            raise ValueError('boom')
        except ValueError:
            logger.exception('something failed')

        payload = json.loads(stream.getvalue().strip().splitlines()[0])
        self.assertEqual(payload['event'], 'something failed')
        self.assertEqual(payload['level'], 'error')
        # exc_info プロセッサが exception を文字列化していること
        self.assertIn('ValueError', payload.get('exception', ''))

    def test_legacy_percent_args_compatible(self):
        """logger.info('%s logged in', 'alice') 形式の既存シグネチャが壊れていないこと."""
        logger, stream = _capture(_build_formatter(json_mode=True))
        logger.info('%s logged in', 'alice')

        payload = json.loads(stream.getvalue().strip())
        self.assertEqual(payload['event'], 'alice logged in')


class StructlogConsoleRenderingTests(SimpleTestCase):
    """DEBUG=True 相当: 人間可読 (ConsoleRenderer) 出力."""

    def test_info_message_renders_as_human_readable(self):
        logger, stream = _capture(_build_formatter(json_mode=False))
        logger.info('event ready')

        output = stream.getvalue().strip()
        self.assertTrue(output, 'ログが出力されること')
        # ConsoleRenderer は JSON ではなくキー=値の整形済み行を吐く
        self.assertNotEqual(output[:1], '{')
        self.assertIn('event ready', output)
        # ログレベル表記が含まれること (大文字小文字どちらかで出る)
        self.assertRegex(output.lower(), r'\binfo\b')

    def test_f_string_message_preserved(self):
        """logger.info(f'User created: {user_id}') 形式の既存スタイル互換."""
        logger, stream = _capture(_build_formatter(json_mode=False))
        user_id = 42
        logger.info(f'User created: {user_id}')

        output = stream.getvalue()
        self.assertIn('User created: 42', output)


class StructlogAssertLogsCompatTests(SimpleTestCase):
    """Django/unittest の assertLogs と互換であることを保証する.

    既存テストが `with self.assertLogs("event", level="ERROR")` で
    `log_ctx.output` をチェックしているため、structlog 化後も同等に
    動作する必要がある。
    """

    def test_assert_logs_captures_message(self):
        logger_name = 'website.tests.structlog.assertlogs'
        with self.assertLogs(logger_name, level='INFO') as log_ctx:
            logging.getLogger(logger_name).info('sentinel message')

        self.assertTrue(any('sentinel message' in line for line in log_ctx.output))
