"""ブログ記事生成を担うモジュール."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from django.db import transaction
from openai import OpenAI
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionNamedToolChoiceParam,
    ChatCompletionToolParam,
)
from openai.types.shared_params import FunctionDefinition
from pydantic import BaseModel, Field, PrivateAttr

from event.models import EventDetail
from event.prompts import BLOG_GENERATION_TEMPLATE
from event.services.media_service import ensure_pdf_thumbnail
from event.services.pdf_worker import PdfWorkerError, run_pdf_worker
from event.services.youtube_service import get_transcript
from website.constants import (
    OPENROUTER_BASE_URL,
    build_openrouter_extra_headers,
)

logger = logging.getLogger(__name__)

# LT1本(15-30分)の要約用途に対し従来の 120k字 x 2 は過大で、入力トークンの支配要因だった。
MAX_SOURCE_TEXT_CHARS = 40_000
# 文字起こし + PDF の合算上限。文字起こしを優先し、PDF は残り予算だけ使う。
MAX_COMBINED_SOURCE_CHARS = 60_000
# 生成結果を保存する時の列の長さ（これを超えると MySQL で DataError になる）
H1_MAX_LENGTH = EventDetail._meta.get_field('h1').max_length
META_DESCRIPTION_MAX_LENGTH = EventDetail._meta.get_field('meta_description').max_length

# save_generated_article の結果
SAVED = 'saved'
EMPTY = 'empty'
REFUSED = 'refused'


@dataclass(frozen=True)
class BlogSources:
    """記事生成に渡す入力（字幕と PDF のテキスト）と、中身を使えた入力の識別子。"""

    transcript: str
    pdf_content: str
    pdf_url: str
    # 字幕を使えた動画の ID と、テキストを使えた PDF の保存名。取れなかった方は空文字
    video_id: str = ''
    slide_name: str = ''

    @property
    def has_text(self) -> bool:
        """字幕か PDF のどちらかのテキストが取れたか。"""
        return bool(self.transcript or self.pdf_content)

    @property
    def used_sources(self) -> tuple[str, str]:
        """生成元として記録する入力（動画 ID, PDF の保存名）。"""
        return self.video_id, self.slide_name


class BlogOutput(BaseModel):
    """ブログ記事の出力形式を定義するPydanticモデル"""
    title: str = Field(description="ブログ記事のタイトル。SEOを意識した40文字以内の魅力的なタイトル。")
    meta_description: str = Field(
        description="ブログ記事のメタディスクリプション。120文字以内でコンテンツの要約を記述。")
    text: str = Field(description="ブログ記事の本文。マークダウン形式で記述された1000〜1800文字の記事。")
    # generate_blog が生成に使った入力。生成元の記録に使う（関数呼び出しのスキーマには出ない）
    _sources: Optional[BlogSources] = PrivateAttr(default=None)

    @property
    def sources(self) -> Optional[BlogSources]:
        """この記事を作るのに使った入力。generate_blog 以外で作った時は None。"""
        return self._sources


def _with_sources(blog_output: BlogOutput, sources: BlogSources) -> BlogOutput:
    """生成結果に、生成に使った入力を持たせる。"""
    blog_output._sources = sources
    return blog_output


def set_generated_article(event_detail: EventDetail, blog_output: BlogOutput,
                          used_sources: tuple[str, str]) -> list[str]:
    """生成した記事を列に入れ、生成元と本文のハッシュを記録して生成待ちの印を外す。

    LLM が列の長さを超えるタイトル・要約を返すことがあるので切り詰める（MySQL で DataError になるため）。

    Returns:
        ``save(update_fields=...)`` に渡す列名（記事の列と、生成の記録の列だけ）。
    """
    event_detail.h1 = blog_output.title[:H1_MAX_LENGTH]
    event_detail.contents = blog_output.text
    event_detail.meta_description = blog_output.meta_description[:META_DESCRIPTION_MAX_LENGTH]
    return [
        'h1', 'contents', 'meta_description', 'updated_at',
        *event_detail.record_generated_article(used_sources=used_sources),
    ]


def save_generated_article(event_detail: EventDetail, blog_output: BlogOutput) -> str:
    """生成ボタンや保存と同時の生成の結果を保存する。

    LLM を待つ間（10〜20 秒）に発表者が記事化を NG に変えることがあるので、行ロックで読み直して
    NG なら書かない。書くのは記事の列と生成の記録の列だけで、呼ぶ前に読んだ古い値で他の列を戻さない。
    サムネイルはストレージに書くので、保存が確定した後に作る。

    生成元に記録するのは、generate_blog が実際に中身を使えた入力だけ（キューと同じ判定）。

    Args:
        event_detail: 記事を作ったイベント詳細
        blog_output: 記事生成結果（generate_blog が使った入力を持つ）

    Returns:
        ``SAVED``（保存した）/ ``EMPTY``（生成結果が空）/ ``REFUSED``（記事化 NG になっていた）
    """
    if not blog_output.title:
        return EMPTY
    used_sources = blog_output.sources.used_sources if blog_output.sources else ('', '')
    with transaction.atomic():
        current = EventDetail.all_objects.select_for_update().get(pk=event_detail.pk)
        if current.is_article_ng:
            return REFUSED
        current.save(update_fields=set_generated_article(current, blog_output, used_sources))
    if not current.thumbnail_image:
        ensure_pdf_thumbnail(current, save=True)
    return SAVED


def _copy_uploaded_file_to_temp_path(uploaded_file, *, suffix: str = '.pdf') -> str:
    """Copy a Django file to a temp path without loading it all into memory."""
    temp_file_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file_path = temp_file.name
            uploaded_file.open('rb')
            for chunk in uploaded_file.chunks():
                temp_file.write(chunk)
        return temp_file_path
    except Exception:
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
        raise
    finally:
        close = getattr(uploaded_file, 'close', None)
        if callable(close):
            close()


def _limit_source_text(text: str, *, max_chars: int = MAX_SOURCE_TEXT_CHARS) -> str:
    """Limit extracted source text before it is embedded in an LLM prompt."""
    if len(text) <= max_chars:
        return text
    logger.info("Source text truncated from %d to %d chars", len(text), max_chars)
    return text[:max_chars]


def _extract_pdf_text(temp_file_path: str, *, max_chars: int = MAX_SOURCE_TEXT_CHARS) -> str:
    """Extract bounded text from a PDF file for blog generation.

    Args:
        temp_file_path: 読み込む PDF の一時ファイルパス
        max_chars: 抽出テキストの合計文字数上限（呼び出し側の残り予算）

    Returns:
        上限内に切り詰めた抽出テキスト
    """
    if max_chars <= 0:
        return ""

    try:
        result = run_pdf_worker("text", temp_file_path, max_chars=max_chars)
        return result.decode("utf-8")
    except PdfWorkerError:
        # Worker failures preserve the existing contract: omit PDF text and
        # continue blog generation with the other available sources.
        return ""


def _get_transcript_with_cache(event_detail: EventDetail) -> Optional[str]:
    """YouTube字幕をキャッシュ優先で取得する.

    同一動画のキャッシュがあれば YouTube API を呼ばずに再利用し、
    取得に成功した時だけキャッシュを更新する（失敗を「字幕なし」として
    恒久化しないため、None のときは書き込まない）。

    Args:
        event_detail: 対象のイベント詳細

    Returns:
        字幕テキスト。取得できなかった場合は None（video_id 未設定時は空文字）
    """
    video_id = event_detail.video_id

    if event_detail.cached_transcript and event_detail.cached_transcript_video_id == video_id:
        logger.info(f"Using cached transcript for video {video_id}: {len(event_detail.cached_transcript)} chars")
        return event_detail.cached_transcript

    transcript = get_transcript(video_id, "ja")
    if transcript:
        logger.info(f"Retrieved transcript for video {video_id}: {len(transcript)} chars")
        event_detail.cached_transcript = transcript
        event_detail.cached_transcript_video_id = video_id or ''
        # save() だと post_save シグナル（twitter の daily_reminder 再同期等）が発火し、
        # 既投稿 TweetQueue を上書き破壊するため、シグナル非発火の update() で書き込む
        EventDetail.objects.filter(pk=event_detail.pk).update(
            cached_transcript=transcript,
            cached_transcript_video_id=video_id or '',
        )
    else:
        logger.warning(f"No transcript found for video {video_id}")

    return transcript


def fetch_transcript(event_detail: EventDetail) -> Optional[str]:
    """YouTube 動画の字幕をキャッシュ優先で取る。動画が無い時は取りに行かずに空文字。

    取った字幕は collect_blog_sources に渡す（取れなかった時に YouTube API を呼び直さないため）。
    """
    if not event_detail.video_id:
        return ''
    return _get_transcript_with_cache(event_detail)


def _read_slide_text(event_detail: EventDetail, max_chars: int) -> str:
    """スライドPDFのテキストを上限内で取り出す。読めない時は空文字。"""
    if not event_detail.slide_file:
        return ""

    temp_file_path = None
    try:
        temp_file_path = _copy_uploaded_file_to_temp_path(event_detail.slide_file)
        pdf_content = _extract_pdf_text(temp_file_path, max_chars=max_chars)
        logger.info(f"Extracted PDF content: {len(pdf_content)} chars")
        return pdf_content
    except Exception as e:
        logger.warning(f"Error loading PDF for EventDetail {event_detail.pk}: {e}")
        return ""
    finally:
        # 一時ファイルを確実に削除
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)


class _NotFetched:
    """字幕をまだ取りに行っていないことを表す（None は「取りに行ったが無かった」なので区別する）。"""


_NOT_FETCHED = _NotFetched()


def collect_blog_sources(event_detail: EventDetail,
                         transcript: Optional[str] | _NotFetched = _NOT_FETCHED) -> BlogSources:
    """記事生成の入力を集める。字幕を優先し、PDF は合算上限の残り予算だけ使う。

    Args:
        event_detail: 対象のイベント詳細
        transcript: 先に取った字幕（fetch_transcript の結果）。渡すと字幕を取り直さない。
            省略時はここで取る

    Returns:
        プロンプトに埋め込む字幕・PDFテキストと、記事に載せるスライドURL
    """
    if isinstance(transcript, _NotFetched):
        # 再生成時のAPI再取得を避けてキャッシュを優先
        transcript = _get_transcript_with_cache(event_detail)
    limited_transcript = _limit_source_text(transcript) if transcript else ""
    pdf_budget = max(MAX_COMBINED_SOURCE_CHARS - len(limited_transcript), 0)
    pdf_url = event_detail.slide_url or (event_detail.slide_file.url if event_detail.slide_file else "")
    pdf_content = _read_slide_text(event_detail, pdf_budget)
    return BlogSources(
        transcript=limited_transcript,
        pdf_content=pdf_content,
        pdf_url=pdf_url,
        video_id=(event_detail.video_id or '') if limited_transcript else '',
        slide_name=event_detail.slide_file.name if pdf_content else '',
    )


def generate_blog(event_detail: EventDetail, model=None, sources: Optional[BlogSources] = None) -> BlogOutput:
    """EventDetailに関連付けられた情報をもとにOpenRouter経由でブログ記事を生成する関数

    Args:
        event_detail (EventDetail): ブログ記事を生成するための情報を含むEventDetailオブジェクト
        model (str): 使用するOpenRouterモデル名。Noneの場合は環境変数から取得
        sources (BlogSources): 呼び出し側で集めた入力。Noneの場合はここで集める

    Returns:
        BlogOutput: タイトル、メタディスクリプション、本文を含むPydanticモデル
    """
    # 環境変数からモデル名を取得（指定がない場合のデフォルト値）
    if model is None:
        model = os.environ.get('GEMINI_MODEL', 'google/gemini-3-flash-preview')
        # `:free`のような接尾辞が付いている場合は削除（OpenRouterでは不要）
        if ':' in model:
            model = model.split(':')[0]
        logger.info(f"Using model from environment: {model}")

    # youtube か slide file がない場合は処理を終了
    if not event_detail.youtube_url and not event_detail.slide_file:
        logger.warning(f"No YouTube URL or slide file provided for EventDetail {event_detail.pk}")
        return BlogOutput(title='', meta_description='', text='')

    # OpenAI SDKを使用してOpenRouterにリクエスト
    try:
        # APIキーを取得
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            logger.warning("OPENROUTER_API_KEY environment variable is not set")
            raise ValueError("OPENROUTER_API_KEY is required")

        logger.info(f"Using OpenRouter with model: {model}")

        # 字幕とPDFのテキスト。自動生成は入力の有無を先に確かめるため、集めた結果を渡してくる
        if sources is None:
            sources = collect_blog_sources(event_detail)

        # プロンプトテンプレートを作成
        prompt_text = BLOG_GENERATION_TEMPLATE.format(
            transcript=sources.transcript or "文字起こしはありません。",
            pdf_content=sources.pdf_content or "PDFコンテンツはありません。",
            date=(
                f"{event_detail.event.date.year}年"
                f"{event_detail.event.date.month}月"
                f"{event_detail.event.date.day}日"
            ) if hasattr(event_detail.event.date, 'strftime') else event_detail.event.date,
            # 日付フォーマット（文字列の場合はそのまま）
            community_name=event_detail.event.community.name,
            speaker=event_detail.speaker,
            theme=event_detail.theme,
            pdf_url=sources.pdf_url or "なし",
            format_instructions=""  # 不要
        )
        # Function Calling 未対応モデルのフォールバック用。フィールド制約は
        # BlogOutput の Field description（= function schema）が正本なのでここでは繰り返さない
        prompt_text += """

# 出力形式
関数呼び出しが使えない場合は、title / meta_description / text の3フィールドを持つ
JSONオブジェクトだけを```json ... ```ブロックで出力してください。"""

        logger.info(f'Prompt for OpenRouter:\n{prompt_text[:500]}...')  # 長すぎるので一部表示

        # OpenAI SDKを使用してOpenRouterにリクエスト
        client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=api_key
        )

        # デバッグ用：APIリクエスト開始時刻を記録
        request_start_time = datetime.now()
        logger.info(f"Starting API request at {request_start_time}")

        try:
            # BlogOutputスキーマを関数定義形式に変換。
            # 必須フィールドは model_json_schema() の parameters.required に含まれるため、
            # 関数定義トップレベルの "required" は持たせない（OpenAI の関数定義スキーマにない冗長キー）。
            blog_output_schema: FunctionDefinition = {
                "name": "generate_blog_post",
                "description": "VRChatイベントの発表内容に基づいてブログ記事を生成する",
                "parameters": BlogOutput.model_json_schema(),
            }
            messages: list[ChatCompletionMessageParam] = [
                {"role": "system",
                 "content": "あなたはVRChatの技術イベントに関するブログ記事を生成する専門のライターです。必ず指定されたJSON形式で出力してください。"},
                {"role": "user", "content": prompt_text},
            ]
            tools: list[ChatCompletionToolParam] = [
                {"type": "function", "function": blog_output_schema}
            ]
            tool_choice: ChatCompletionNamedToolChoiceParam = {
                "type": "function",
                "function": {"name": "generate_blog_post"},
            }

            # Function Callingを使用したリクエスト
            completion = client.chat.completions.create(
                extra_headers=build_openrouter_extra_headers(),
                model=model,
                messages=messages,
                temperature=0.3,  # 温度を下げて出力の安定性を向上
                max_tokens=5000,
                tools=tools,
                tool_choice=tool_choice,
            )

            # デバッグ用：APIリクエスト終了時刻とかかった時間を記録
            request_end_time = datetime.now()
            request_duration = (request_end_time - request_start_time).total_seconds()
            logger.info(f"API request completed in {request_duration:.2f} seconds")

        except Exception as api_error:
            logger.error(f"API request failed: {str(api_error)}")
            # API URL情報を詳細に記録
            logger.error(f"Request details: base_url={OPENROUTER_BASE_URL}, model={model}")
            raise  # 例外を再スロー

        # Function Callingのレスポンスを処理
        try:
            # ツール呼び出しの確認
            message = completion.choices[0].message

            # ツール呼び出しの結果がある場合
            if hasattr(message, 'tool_calls') and message.tool_calls:
                tool_call = message.tool_calls[0]
                # 関数レスポンスのJSONを取得
                blog_output_json = tool_call.function.arguments
                logger.info(f"Raw response from Function Call: {blog_output_json[:500]}...")

                # 直接Pydanticモデルに変換を試みる
                try:
                    blog_output = BlogOutput.model_validate_json(blog_output_json)
                    logger.info("Successfully parsed BlogOutput from function call response")
                    return _with_sources(blog_output, sources)
                except Exception as validate_error:
                    logger.warning(f"Failed to validate BlogOutput from function call: {str(validate_error)}")
                    # 検証に失敗した場合、JSONとして解析して手動でモデルを作成
                    try:
                        output_data = json.loads(blog_output_json)
                        blog_output = BlogOutput(**output_data)
                        logger.info("Created BlogOutput manually from function call data")
                        return _with_sources(blog_output, sources)
                    except Exception as e:
                        logger.error(f"Failed to parse function call response: {str(e)}")
                        # 失敗した場合は通常のJSONパース処理に続く
            else:
                # Function Callingがサポートされていない場合、通常のコンテンツレスポンスになる
                logger.warning("No tool_calls in response. Model might not support function calling.")

            # レスポンスからテキストを取得（Function Calling未対応の場合のフォールバック）
            response_text = message.content
            # content が空なら tool_calls も content も無い異常応答なので、後段で
            # 曖昧に落ちる前にここで失敗させる
            if not response_text:
                raise ValueError("OpenRouter response has neither tool_calls nor content")
            logger.info(f"Raw response from OpenRouter:\n{response_text[:500]}...")

            # 以下はJSON抽出の既存コード
            # 応答テキストからJSON部分を抽出（```json ... ``` のようなマークダウンを考慮）
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
                logger.info(f"Extracted JSON from markdown block: {len(json_str)} chars")
            else:
                # JSONマークダウンが見つからない場合は、応答全体がJSONであると仮定
                logger.warning(
                    "JSON markdown block (```json ... ```) not found in the response. Attempting to parse the entire response.")
                json_str = response_text.strip()  # 前後の空白を除去
                logger.info(f"Using entire response as JSON: {len(json_str)} chars")

            # 既存のJSON処理コードをそのまま使用
            # エスケープシーケンスを正規化して修正
            normalized_json = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', json_str)
            logger.info(f"Normalized JSON string length: {len(normalized_json)}")

            # または別の方法として、すべての不正なエスケープを削除する
            clean_json = re.sub(r'\\([^"\\/bfnrtu])', r'\1', json_str)
            logger.info(f"Cleaned JSON string length: {len(clean_json)}")

            # 制御文字を処理
            control_chars_removed = re.sub(r'[\x00-\x1F\x7F]', '', json_str)
            logger.info(f"Control characters removed JSON length: {len(control_chars_removed)}")

            output_data = None
            error_messages = []

            try:
                # まず正規化されたJSONで試す
                logger.info("Attempting to parse normalized JSON")
                output_data = json.loads(normalized_json)
                logger.info("Successfully parsed normalized JSON")
            except json.JSONDecodeError as e:
                error_messages.append(f"Normalized JSON parse error: {str(e)}")
                logger.warning(f"Failed to parse normalized JSON: {str(e)}")

                # それでも失敗したら、クリーニングされたJSONで試す
                try:
                    logger.info("Attempting to parse cleaned JSON")
                    output_data = json.loads(clean_json)
                    logger.info("Successfully parsed cleaned JSON")
                except json.JSONDecodeError as e:
                    error_messages.append(f"Cleaned JSON parse error: {str(e)}")
                    logger.warning(f"Failed to parse cleaned JSON: {str(e)}")

                    # 制御文字を削除したJSONで試す
                    try:
                        logger.info("Attempting to parse JSON with control characters removed")
                        output_data = json.loads(control_chars_removed)
                        logger.info("Successfully parsed JSON with control characters removed")
                    except json.JSONDecodeError as e:
                        error_messages.append(f"Control chars removed JSON parse error: {str(e)}")
                        logger.warning(f"Failed to parse JSON with control characters removed: {str(e)}")

                        # それでも失敗した場合、より積極的な方法で処理
                        try:
                            # バックスラッシュと制御文字を全て削除し、文字列リテラルとして解析を試みる
                            logger.info(
                                "Attempting aggressive cleaning by removing all backslashes and control characters")
                            clean_json_aggressive = re.sub(r'[\x00-\x1F\x7F]', '', json_str.replace('\\', ''))
                            output_data = json.loads(clean_json_aggressive)
                            logger.info("Successfully parsed aggressively cleaned JSON")
                        except json.JSONDecodeError as e:
                            error_messages.append(f"Aggressive clean JSON parse error: {str(e)}")
                            logger.error(f"All JSON parsing attempts failed. Last error: {str(e)}")
                            # このポイントで全ての解析が失敗した場合、JSONの一部を表示してデバッグを支援
                            logger.error(f"JSON string excerpt: {json_str[:200]}...")
                            raise json.JSONDecodeError(
                                f"Unable to parse JSON after multiple attempts: {'; '.join(error_messages)}", json_str,
                                0)

            if output_data is None:
                raise ValueError("JSON parsing succeeded but produced None result")

            # BlogOutputモデルに変換
            try:
                blog_output = BlogOutput(**output_data)
                logger.info('Parsed BlogOutput: ' + str(blog_output))
                return _with_sources(blog_output, sources)
            except Exception as validation_error:
                # Pydanticのバリデーションエラーなど
                logger.error(f"Failed to create BlogOutput from parsed JSON: {str(validation_error)}")
                logger.error(f"Parsed data: {output_data}")
                raise

        except Exception as process_error:
            # silent failure: 空 BlogOutput を返して呼び出し側のフォールバックに渡す。
            # 既存ログメッセージは互換のため残しつつ silent_failure を追加で発火する。
            logger.error(f"Error processing response: {str(process_error)}")
            logger.exception(
                "silent_failure",
                extra={
                    "event_type": "blog_generation_response_processing_failed",
                    "event_detail_id": event_detail.pk,
                    "is_silent": True,
                },
            )
            return BlogOutput(title='', meta_description='', text='')

    except Exception as e:
        # APIキーエラーなどもここで捕捉される可能性がある
        logger.error(f"Error calling OpenRouter or processing response for EventDetail {event_detail.pk}: {e}")
        if "API key" in str(e):
            logger.error("OPENROUTER_API_KEY environment variable might be missing or invalid.")
        # silent failure: OpenRouter 呼び出し全体の失敗を Sentry に流すための構造化ログ。
        logger.exception(
            "silent_failure",
            extra={
                "event_type": "blog_generation_openrouter_call_failed",
                "event_detail_id": event_detail.pk,
                "is_silent": True,
            },
        )
        return BlogOutput(title='', meta_description='', text='')
