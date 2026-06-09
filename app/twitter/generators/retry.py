"""ツイート生成のリトライラッパー。

LLM 生成関数を文字数バリデーション付きで実行し、違反時は target_chars を
段階的に減らしてリトライ。最終フォールバックとして決定的圧縮関数を呼ぶ。
"""

import inspect
import logging
from typing import Any, Callable, Optional

from twitter.generators.common import (
    RETRY_TARGET_CHARS_STEP,
    _validation_feedback,
    count_body_lines,
    count_tweet_length,
    is_tweet_text_valid,
    validate_tweet_text,
)

logger = logging.getLogger(__name__)


def _call_generate_fn(
    generate_fn: Callable[..., Optional[str]],
    *args: Any,
    target_chars: int,
    validation_feedback: str,
    **kwargs: Any,
) -> Optional[str]:
    parameters = inspect.signature(generate_fn).parameters
    accepts_feedback = (
        "validation_feedback" in parameters
        or any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())
    )
    if accepts_feedback:
        return generate_fn(
            *args,
            target_chars=target_chars,
            validation_feedback=validation_feedback,
            **kwargs,
        )
    return generate_fn(*args, target_chars=target_chars, **kwargs)


def _generate_with_retry(
    generate_fn: Callable[..., Optional[str]],
    *args: Any,
    max_retries: int = 3,
    fallback_fn: Optional[Callable[..., Optional[str]]] = None,
    **kwargs: Any,
) -> Optional[str]:
    """生成関数をリトライラッパーで実行する。

    1. target_chars=140 で生成
    2. count_tweet_length() と count_body_lines() でバリデーション
       （文字数 <= TWEET_MAX_WEIGHTED_LENGTH かつ 本文行数 <= MAX_BODY_LINES）
    3. どちらか違反していたら target_chars を RETRY_TARGET_CHARS_STEP ずつ減らしてリトライ
    4. max_retries 回リトライ後も違反している場合は決定的な圧縮にフォールバック
    """
    target_chars = 140
    result = None
    validation_feedback = ""

    for attempt in range(max_retries + 1):
        result = _call_generate_fn(
            generate_fn,
            *args,
            target_chars=target_chars,
            validation_feedback=validation_feedback,
            **kwargs,
        )
        if result is None:
            return None

        errors = validate_tweet_text(result)

        if not errors:
            if attempt > 0:
                logger.info(
                    "Tweet validation OK after %d retries (weighted=%d, body_lines=%d, target_chars=%d)",
                    attempt,
                    count_tweet_length(result),
                    count_body_lines(result),
                    target_chars,
                )
            return result

        logger.warning(
            "Tweet validation failed (%s, attempt=%d/%d, target_chars=%d). Retrying.",
            ", ".join(errors),
            attempt + 1,
            max_retries + 1,
            target_chars,
        )
        validation_feedback = _validation_feedback(result)
        target_chars -= RETRY_TARGET_CHARS_STEP

    if fallback_fn is None:
        return None

    fallback_result = fallback_fn(*args)
    if fallback_result and is_tweet_text_valid(fallback_result):
        logger.info(
            "Tweet deterministic fallback succeeded (weighted=%d, body_lines=%d)",
            count_tweet_length(fallback_result),
            count_body_lines(fallback_result),
        )
        return fallback_result

    logger.error(
        "Tweet deterministic fallback failed after generation retries: %s",
        validate_tweet_text(fallback_result or ""),
    )
    return None
