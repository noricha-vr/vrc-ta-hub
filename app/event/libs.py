import json
import logging
import os
import re
import tempfile
from datetime import datetime
from typing import Optional

import bleach
import markdown
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from openai import OpenAI
from pydantic import BaseModel, Field
from pypdf import PdfReader
from youtube_transcript_api import YouTubeTranscriptApi

from event.models import EventDetail
from event.prompts import BLOG_GENERATION_TEMPLATE
from website.settings import GOOGLE_API_KEY

logger = logging.getLogger(__name__)


class BlogOutput(BaseModel):
    """ブログ記事の出力形式を定義するPydanticモデル"""
    title: str = Field(description="ブログ記事のタイトル。SEOを意識した40文字以内の魅力的なタイトル。")
    meta_description: str = Field(
        description="ブログ記事のメタディスクリプション。120文字以内でコンテンツの要約を記述。")
    text: str = Field(description="ブログ記事の本文。マークダウン形式で記述された1000〜1800文字の記事。")


def generate_blog(event_detail: EventDetail, model=None) -> BlogOutput:
    """
    EventDetailに関連付けられた情報をもとにOpenRouter経由でブログ記事を生成する関数

    Args:
        event_detail (EventDetail): ブログ記事を生成するための情報を含むEventDetailオブジェクト
        model (str): 使用するOpenRouterモデル名。Noneの場合は環境変数から取得

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

        # YouTube動画から文字起こしを取得
        transcript = get_transcript(event_detail.video_id, "ja")
        if transcript:
            logger.info(f"Retrieved transcript for video {event_detail.video_id}: {len(transcript)} chars")
        else:
            logger.warning(f"No transcript found for video {event_detail.video_id}")

        # PDFの内容とURLを取得
        pdf_content = ""
        pdf_url = event_detail.slide_url or (event_detail.slide_file.url if event_detail.slide_file else "")

        if event_detail.slide_file:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
                event_detail.slide_file.seek(0)
                temp_file.write(event_detail.slide_file.read())
                temp_file_path = temp_file.name

            try:
                # PyPDFを使用してPDFの内容を抽出
                reader = PdfReader(temp_file_path)
                pdf_content = "\n".join([page.extract_text() or "" for page in reader.pages])
                logger.info(f"Extracted PDF content: {len(pdf_content)} chars")
            except Exception as e:
                logger.warning(f"Error loading PDF for EventDetail {event_detail.pk}: {e}")
            finally:
                # 一時ファイルを確実に削除
                if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)

        # プロンプトテンプレートを作成
        prompt_text = BLOG_GENERATION_TEMPLATE.format(
            transcript=transcript or "文字起こしはありません。",  # 空の場合の代替テキスト
            pdf_content=pdf_content or "PDFコンテンツはありません。",  # 空の場合の代替テキスト
            date=event_detail.event.date.strftime('%Y年%m月%d日') if hasattr(event_detail.event.date,
                                                                             'strftime') else event_detail.event.date,
            # 日付フォーマット（文字列の場合はそのまま）
            community_name=event_detail.event.community.name,
            speaker=event_detail.speaker,
            theme=event_detail.theme,
            pdf_url=pdf_url or "なし",
            format_instructions=""  # 不要
        )
        # プロンプトにJSON出力指示を追加
        prompt_text += """

# 重要な出力形式の指示
必ず以下のフォーマットで出力してください。

1. 最初に```jsonと書く
2. 次の行からJSONオブジェクトを開始
3. 3つのフィールド（title, meta_description, text）を必ず含める
4. 最後に```で閉じる
5. それ以外のテキストは一切含めない

出力例:
```json
{
  "title": "40文字以内のSEOを意識した魅力的なタイトル",
  "meta_description": "120文字以内のコンテンツ要約",
  "text": "マークダウン形式の1000〜2300文字の本文"
}
```

注意事項:
- titleは必ず40文字以内
- meta_descriptionは必ず120文字以内
- textは必ず1000文字以上2300文字以内
- 各フィールドは空にしない
- JSON内のダブルクォート文字は\"でエスケープする
- 改行は\nで表現する"""

        logger.info(f'Prompt for OpenRouter:\n{prompt_text[:500]}...')  # 長すぎるので一部表示

        # OpenAI SDKを使用してOpenRouterにリクエスト
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key
        )

        # デバッグ用：APIリクエスト開始時刻を記録
        request_start_time = datetime.now()
        logger.info(f"Starting API request at {request_start_time}")

        try:
            # BlogOutputスキーマを関数定義形式に変換
            blog_output_schema = {
                "name": "generate_blog_post",
                "description": "VRChatイベントの発表内容に基づいてブログ記事を生成する",
                "parameters": BlogOutput.model_json_schema(),
                "required": ["title", "meta_description", "text"]
            }

            # Function Callingを使用したリクエスト
            completion = client.chat.completions.create(
                extra_headers={
                    "HTTP-Referer": "https://vrc-ta-hub.com/",  # OpenRouterのランキング用サイトURL
                    "X-Title": "VRC TA Hub"  # OpenRouterのランキング用サイト名
                },
                model=model,
                messages=[
                    {"role": "system",
                     "content": "あなたはVRChatの技術イベントに関するブログ記事を生成する専門のライターです。必ず指定されたJSON形式で出力してください。"},
                    {"role": "user", "content": prompt_text}
                ],
                temperature=0.3,  # 温度を下げて出力の安定性を向上
                max_tokens=5000,
                tools=[{"type": "function", "function": blog_output_schema}],
                tool_choice={"type": "function", "function": {"name": "generate_blog_post"}}
            )

            # デバッグ用：APIリクエスト終了時刻とかかった時間を記録
            request_end_time = datetime.now()
            request_duration = (request_end_time - request_start_time).total_seconds()
            logger.info(f"API request completed in {request_duration:.2f} seconds")

        except Exception as api_error:
            logger.error(f"API request failed: {str(api_error)}")
            # API URL情報を詳細に記録
            logger.error(f"Request details: base_url=https://openrouter.ai/api/v1, model={model}")
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
                    logger.info(f"Successfully parsed BlogOutput from function call response")
                    return blog_output
                except Exception as validate_error:
                    logger.warning(f"Failed to validate BlogOutput from function call: {str(validate_error)}")
                    # 検証に失敗した場合、JSONとして解析して手動でモデルを作成
                    try:
                        output_data = json.loads(blog_output_json)
                        blog_output = BlogOutput(**output_data)
                        logger.info(f"Created BlogOutput manually from function call data")
                        return blog_output
                    except Exception as e:
                        logger.error(f"Failed to parse function call response: {str(e)}")
                        # 失敗した場合は通常のJSONパース処理に続く
            else:
                # Function Callingがサポートされていない場合、通常のコンテンツレスポンスになる
                logger.warning("No tool_calls in response. Model might not support function calling.")

            # レスポンスからテキストを取得（Function Calling未対応の場合のフォールバック）
            response_text = message.content
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
                return blog_output
            except Exception as validation_error:
                # Pydanticのバリデーションエラーなど
                logger.error(f"Failed to create BlogOutput from parsed JSON: {str(validation_error)}")
                logger.error(f"Parsed data: {output_data}")
                raise

        except Exception as process_error:
            logger.error(f"Error processing response: {str(process_error)}")
            # レスポンス処理エラー時は空のBlogOutputを返す
            return BlogOutput(title='', meta_description='', text='')

    except Exception as e:
        # APIキーエラーなどもここで捕捉される可能性がある
        logger.error(f"Error calling OpenRouter or processing response for EventDetail {event_detail.pk}: {e}")
        if "API key" in str(e):
            logger.error("OPENROUTER_API_KEY environment variable might be missing or invalid.")
        return BlogOutput(title='', meta_description='', text='')


def get_transcript(video_id, language='ja') -> Optional[str]:
    """
    YouTube動画から文字起こしを取得する関数

    Args:
      video_id: YouTube動画のID
      language: 文字起こしの言語のリスト。デフォルトは日本語

    Returns:
      文字起こしテキスト
    """
    if not video_id:
        return ''

    try:
        # APIキーを設定
        youtube = build('youtube', 'v3', developerKey=GOOGLE_API_KEY)

        # 動画の詳細情報を取得
        video_response = youtube.videos().list(
            part='snippet',
            id=video_id
        ).execute()

        if not video_response['items']:
            raise ValueError('動画が見つかりませんでした')

        # 字幕を取得 (認証不要)
        transcript_list = YouTubeTranscriptApi.list_transcripts(
            video_id)

        # 日本語字幕を優先的に取得し、なければ英語字幕を取得して翻訳
        try:
            transcript = transcript_list.find_transcript(['ja'])
        except:
            transcript = transcript_list.find_transcript(
                ['en']).translate('ja')

        # 字幕テキストを結合
        captions_text = "\n".join([entry['text']
                                   for entry in transcript.fetch()])
        return captions_text

    except Exception as e:
        logger.error(f"Youtubeから文字起こしを取得するときにエラーが発生しました: {str(e)}")
        return None


def _escape_unknown_html_tags(text: str) -> str:
    """Markdown変換前に、許可されていないHTMLタグ形式の文字列をエスケープする

    P-AMI<Q>のような技術用語内の<>がHTMLタグとして解釈されるのを防ぐ。
    コードブロックやインラインコード内のタグは保護される。

    Args:
        text: 入力テキスト（Markdown形式）

    Returns:
        エスケープ処理されたテキスト
    """
    # 許可されたHTMLタグのリスト（convert_markdownのallowed_tagsと同期）
    allowed_tags = {
        'a', 'p', 'h1', 'h2', 'h3', 'h4', 'ul', 'ol', 'li', 'strong', 'em',
        'code', 'pre', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr',
        'br', 'blockquote', 'div', 'iframe', 'span', 'img'
    }

    # コードブロックを一時的に保護（```...```）
    code_blocks = []

    def protect_code_block(match):
        code_blocks.append(match.group(0))
        return f'\x00CODE_BLOCK_{len(code_blocks) - 1}\x00'

    text = re.sub(r'```[\s\S]*?```', protect_code_block, text)

    # インラインコードを一時的に保護（`...`）
    inline_codes = []

    def protect_inline_code(match):
        inline_codes.append(match.group(0))
        return f'\x00INLINE_CODE_{len(inline_codes) - 1}\x00'

    text = re.sub(r'`[^`]+`', protect_inline_code, text)

    # HTMLタグパターンをエスケープ（許可タグ以外）
    def escape_tag(match):
        full_match = match.group(0)
        tag_name = match.group(1).lower()

        if tag_name in allowed_tags:
            return full_match
        return full_match.replace('<', '&lt;').replace('>', '&gt;')

    # 開始タグ・自己閉じタグ: <tagname...>
    text = re.sub(r'<([a-zA-Z][a-zA-Z0-9]*)[^>]*/?>', escape_tag, text)

    # 閉じタグ: </tagname>
    def escape_closing_tag(match):
        full_match = match.group(0)
        tag_name = match.group(1).lower()

        if tag_name in allowed_tags:
            return full_match
        return full_match.replace('<', '&lt;').replace('>', '&gt;')

    text = re.sub(r'</([a-zA-Z][a-zA-Z0-9]*)>', escape_closing_tag, text)

    # 保護したコードを復元
    for i, block in enumerate(code_blocks):
        text = text.replace(f'\x00CODE_BLOCK_{i}\x00', block)

    for i, code in enumerate(inline_codes):
        text = text.replace(f'\x00INLINE_CODE_{i}\x00', code)

    return text


def convert_markdown(markdown_text: str, auto_format: bool = False) -> str:
    """MarkdownをHTMLに変換し、サニタイズする
    
    Args:
        markdown_text: 変換するMarkdownテキスト
        auto_format: 自動整形を行うかどうか（デフォルト: False）
    """
    logger.debug("Original markdown text:")
    logger.debug(markdown_text)

    # 未知のHTMLタグをエスケープ（P-AMI<Q>等の対策）
    markdown_text = _escape_unknown_html_tags(markdown_text)

    # 改行を正規化
    markdown_text = markdown_text.replace('\r\n', '\n').replace('\r', '\n')
    
    # 最初の行が # で始まる場合は除去（H1タグの重複を防ぐ）
    lines = markdown_text.split('\n')
    if lines and lines[0].strip().startswith('# '):
        lines = lines[1:]
        markdown_text = '\n'.join(lines).lstrip()

    # auto_formatがTrueの場合のみ、句読点での自動整形を行う
    if auto_format:
        # 各行の先頭スペースを削除（リストを除く）
        lines = markdown_text.split('\n')
        normalized_lines = []
        in_list = False

        for line in lines:
            # リストアイテムの検出
            if line.lstrip().startswith(('- ', '* ', '+ ', '1. ', '2. ', '3. ')):
                in_list = True
                normalized_lines.append(line)
            elif line.strip() == '':
                in_list = False
                normalized_lines.append(line)
            else:
                if in_list:
                    normalized_lines.append(line)
                else:
                    # 非リスト行の場合、文を分割して空行を追加
                    sentences = re.split(r'([。！？])', line.lstrip())
                    for i in range(0, len(sentences) - 1, 2):
                        if sentences[i].strip():
                            normalized_lines.append(
                                sentences[i] + (sentences[i + 1] if i + 1 < len(sentences) else ''))
                            normalized_lines.append('')  # 空行を追加
                    if sentences[-1].strip() and not sentences[-1][-1] in '。！？':
                        normalized_lines.append(sentences[-1])

        markdown_text = '\n'.join(normalized_lines)

        # 連続する空行を2行に制限
        markdown_text = re.sub(r'\n{3,}', '\n\n', markdown_text)

        # 感嘆符・疑問符と閉じ括弧または絵文字の間の改行を削除
        # Unicode絵文字のパターン
        emoji_pattern = r'[\U0001F300-\U0001F9FF\u200d\u2600-\u26FF\u2700-\u27BF]'
        # 改行を削除するパターン
        markdown_text = re.sub(
            rf'([！!？?])\n+((?:{emoji_pattern}+|[」）\)]))',
            r'\1\2',
            markdown_text
        )
    else:
        # 標準的なMarkdown処理
        # 連続する空行を2行に制限（段落区切りの正規化）
        markdown_text = re.sub(r'\n{3,}', '\n\n', markdown_text)

    logger.debug("Normalized markdown text:")
    logger.debug(markdown_text)

    allowed_tags = ['a', 'p', 'h1', 'h2', 'h3', 'h4', 'ul', 'ol', 'li', 'strong', 'em', 'code', 'pre', 'table', 'thead',
                    'tbody', 'tr', 'th', 'td', 'hr', 'br', 'blockquote', 'div', 'iframe']
    allowed_attributes = {'a': ['href', 'title'],
                          'pre': ['class'], 'table': ['class'],
                          'div': ['class'],
                          'iframe': ['src', 'frameborder', 'allowfullscreen', 'width', 'height',
                                     'referrerpolicy', 'allow']}

    # Markdownの拡張機能を追加
    extensions = [
        'tables',
        'nl2br',  # 改行を<br>に変換
        'fenced_code',  # コードブロックのサポート
    ]

    # マークダウンをHTMLに変換
    html = markdown.markdown(markdown_text, extensions=extensions)

    logger.debug("Generated HTML before BeautifulSoup:")
    logger.debug(html)

    # BeautifulSoupを使ってHTMLをパース
    soup = BeautifulSoup(html, 'html.parser')

    # テーブルタグにクラスを追加
    for table in soup.find_all('table'):
        table['class'] = table.get('class', []) + ['table', 'table-responsive']

    # YouTubeリンクを埋め込みプレーヤーに変換
    # パターン1: https://www.youtube.com/watch?v=VIDEO_ID
    # パターン2: https://youtu.be/VIDEO_ID
    youtube_patterns = [
        (r'https?://www\.youtube\.com/watch\?v=([a-zA-Z0-9_-]+)', r'https://www.youtube.com/embed/\1'),
        (r'https?://youtu\.be/([a-zA-Z0-9_-]+)', r'https://www.youtube.com/embed/\1')
    ]
    
    # まず、テキスト内の単純なURLを検索して<a>タグに変換
    import copy
    for p in soup.find_all(['p', 'li']):
        # タグ内の全テキストを取得
        if p.get_text():
            # 子要素を保持しながら処理
            new_contents = []
            for content in p.contents:
                if isinstance(content, str):
                    # テキストノードの場合、YouTube URLを検索
                    text = content
                    modified = False
                    for pattern, embed_url_pattern in youtube_patterns:
                        if re.search(pattern, text):
                            # URLを<a>タグに置換
                            text = re.sub(pattern, r'<a href="\g<0>">\g<0></a>', text)
                            modified = True
                    
                    if modified:
                        # 新しいHTMLとして解析
                        temp_soup = BeautifulSoup(text, 'html.parser')
                        new_contents.extend(temp_soup.contents)
                    else:
                        new_contents.append(content)
                else:
                    # タグの場合はそのまま追加
                    new_contents.append(copy.copy(content))
            
            # 元の内容を新しい内容で置き換え
            p.clear()
            for content in new_contents:
                p.append(content)
    
    # すべてのリンクを検索して埋め込みに変換
    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        for pattern, embed_url_pattern in youtube_patterns:
            match = re.match(pattern, href)
            if match:
                # YouTube埋め込みコンテナを作成（2025年仕様対応）
                container_div = soup.new_tag('div', **{'class': 'youtube-embed-container'})
                iframe = soup.new_tag('iframe',
                                    src=re.sub(pattern, embed_url_pattern, href),
                                    frameborder='0',
                                    allowfullscreen=True,
                                    referrerpolicy='strict-origin-when-cross-origin',
                                    allow='accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share')
                container_div.append(iframe)
                # リンクをコンテナに置き換え
                link.replace_with(container_div)
                break

    # パース後のHTMLを文字列に変換
    html = str(soup)

    logger.debug("Generated HTML before sanitization:")
    logger.debug(html)

    # HTMLをサニタイズして返す
    sanitized_html = bleach.clean(
        html, tags=allowed_tags, attributes=allowed_attributes)

    logger.debug("Final sanitized HTML:")
    logger.debug(sanitized_html)

    return sanitized_html
