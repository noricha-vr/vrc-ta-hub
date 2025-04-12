import logging
import os
import tempfile
from typing import Optional
import re
import json
from openai import OpenAI

import bleach
import markdown
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from pypdf import PdfReader
from pydantic import BaseModel, Field
from youtube_transcript_api import YouTubeTranscriptApi

from event.models import EventDetail
from website.settings import GOOGLE_API_KEY
from event.prompts import BLOG_GENERATION_TEMPLATE

logger = logging.getLogger(__name__)


class BlogOutput(BaseModel):
    """ブログ記事の出力形式を定義するPydanticモデル"""
    title: str = Field(description="ブログ記事のタイトル。SEOを意識した40文字以内の魅力的なタイトル。")
    meta_description: str = Field(description="ブログ記事のメタディスクリプション。120文字以内でコンテンツの要約を記述。")
    text: str = Field(description="ブログ記事の本文。マークダウン形式で記述された1000〜1800文字の記事。")


def generate_blog(event_detail: EventDetail, model='google/gemini-2.0-flash-001') -> BlogOutput:
    """
    EventDetailに関連付けられた情報をもとにOpenRouter経由でブログ記事を生成する関数

    Args:
        event_detail (EventDetail): ブログ記事を生成するための情報を含むEventDetailオブジェクト
        model (str): 使用するOpenRouterモデル名 (例: 'google/gemini-2.0-flash-001')

    Returns:
        BlogOutput: タイトル、メタディスクリプション、本文を含むPydanticモデル
    """
    # youtube か slide file がない場合は処理を終了
    if not event_detail.youtube_url and not event_detail.slide_file:
        return BlogOutput(title='', meta_description='', text='')

    # テスト環境チェック
    is_testing = 'TESTING' in os.environ or (event_detail.event and event_detail.event.community and 'test' in event_detail.event.community.name.lower())
    if is_testing:
        logger.info("テスト環境のため、モックレスポンスを返します")
        return BlogOutput(
            title="テストタイトル",
            meta_description="テストのメタ説明",
            text="テスト本文の内容"
        )

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
            except Exception as e:
                logger.warning(f"Error loading PDF for EventDetail {event_detail.pk}: {e}")
            finally:
                if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)

        # プロンプトテンプレートを作成
        prompt_text = BLOG_GENERATION_TEMPLATE.format(
            transcript=transcript or "文字起こしはありません。", # 空の場合の代替テキスト
            pdf_content=pdf_content or "PDFコンテンツはありません。", # 空の場合の代替テキスト
            date=event_detail.event.date.strftime('%Y年%m月%d日') if hasattr(event_detail.event.date, 'strftime') else event_detail.event.date, # 日付フォーマット（文字列の場合はそのまま）
            community_name=event_detail.event.community.name,
            speaker=event_detail.speaker,
            theme=event_detail.theme,
            pdf_url=pdf_url or "なし",
            format_instructions="" # 不要
        )
        # プロンプトにJSON出力指示を追加
        prompt_text += """

# 出力形式
以下のJSON形式で出力してください。マークダウンの```json ... ```ブロックで囲んでください。他のテキストは含めないでください。
```json
{
 "title": "ブログ記事のタイトル。SEOを意識した40文字以内の魅力的なタイトル。",
 "meta_description": "ブログ記事のメタディスクリプション。120文字以内でコンテンツの要約を記述。",
 "text": "ブログ記事の本文。マークダウン形式で記述された1000〜1800文字の記事。"
}
```"""

        logger.info(f'Prompt for OpenRouter:\n{prompt_text[:500]}...') # 長すぎるので一部表示

        # OpenAI SDKを使用してOpenRouterにリクエスト
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key
        )
        
        # ヘッダーを追加してリクエスト
        completion = client.chat.completions.create(
            extra_headers={
                "HTTP-Referer": "https://vrc-ta-hub.com/",  # OpenRouterのランキング用サイトURL
                "X-Title": "VRC TA Hub"  # OpenRouterのランキング用サイト名
            },
            model=model,
            messages=[
                {"role": "user", "content": prompt_text}
            ],
            temperature=0.7,
            max_tokens=2000
        )
        
        # レスポンスからテキストを取得
        response_text = completion.choices[0].message.content
        logger.info(f"Raw response from OpenRouter:\n{response_text[:500]}...")

        # 応答テキストからJSON部分を抽出（```json ... ``` のようなマークダウンを考慮）
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', response_text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # JSONマークダウンが見つからない場合は、応答全体がJSONであると仮定
            logger.warning("JSON markdown block (```json ... ```) not found in the response. Attempting to parse the entire response.")
            json_str = response_text.strip() # 前後の空白を除去

        # JSON文字列をパース
        try:
            # エスケープシーケンスを正規化して修正
            # バックスラッシュが単独で現れる場合に対処
            normalized_json = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', json_str)
            # または別の方法として、すべての不正なエスケープを削除する
            clean_json = re.sub(r'\\([^"\\/bfnrtu])', r'\1', json_str)
            
            try:
                # まず正規化されたJSONで試す
                output_data = json.loads(normalized_json)
            except json.JSONDecodeError:
                # それでも失敗したら、クリーニングされたJSONで試す
                try:
                    output_data = json.loads(clean_json)
                except json.JSONDecodeError:
                    # それでも失敗した場合、より積極的な方法で処理
                    # バックスラッシュを全て削除し、文字列リテラルとして解析を試みる
                    clean_json_aggressive = json_str.replace('\\', '')
                    output_data = json.loads(clean_json_aggressive)
            
            # BlogOutputモデルに変換
            blog_output = BlogOutput(**output_data)
            logger.info('Parsed BlogOutput: ' + str(blog_output))
            return blog_output
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON response from OpenRouter: {e}\nAttempted JSON string: {json_str}")
            # JSONパース失敗時は空を返す
            return BlogOutput(title='', meta_description='', text='')
        except Exception as e: # Pydanticのバリデーションエラーなども捕捉
            logger.error(f"Failed to create BlogOutput from parsed JSON: {e}\nParsed data attempt: {json_str}")
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


def convert_markdown(markdown_text: str) -> str:
    """MarkdownをHTMLに変換し、サニタイズする"""
    logger.debug("Original markdown text:")
    logger.debug(markdown_text)
    
    # 改行を正規化
    markdown_text = markdown_text.replace('\r\n', '\n').replace('\r', '\n')
    
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
                for i in range(0, len(sentences)-1, 2):
                    if sentences[i].strip():
                        normalized_lines.append(sentences[i] + (sentences[i+1] if i+1 < len(sentences) else ''))
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
    
    logger.debug("Normalized markdown text:")
    logger.debug(markdown_text)
    
    allowed_tags = ['a', 'p', 'h1', 'h2', 'h3', 'h4', 'ul', 'ol', 'li', 'strong', 'em', 'code', 'pre', 'table', 'thead',
                    'tbody', 'tr', 'th', 'td', 'hr', 'br', 'blockquote']
    allowed_attributes = {'a': ['href', 'title'], 'pre': ['class'], 'table': ['class']}
    
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
    
    # パース後のHTMLを文字列に変換
    html = str(soup)
    
    logger.debug("Generated HTML before sanitization:")
    logger.debug(html)
    
    # HTMLをサニタイズして返す
    sanitized_html = bleach.clean(html, tags=allowed_tags, attributes=allowed_attributes)
    
    logger.debug("Final sanitized HTML:")
    logger.debug(sanitized_html)
    
    return sanitized_html


def generate_meta_description(text: str, model='google/gemini-2.0-flash-001') -> str:
    """
    OpenRouterを使用してブログ記事のテキストからメタディスクリプションを生成する関数

    Args:
        text (str): ブログ記事の全文テキスト
        model (str): 使用するOpenRouterモデル名

    Returns:
        str: 生成されたメタディスクリプション（120文字以内）
    """
    # テスト環境チェック
    if 'TESTING' in os.environ:
        logger.info("テスト環境のため、モックメタディスクリプションを返します")
        return "テスト用のメタディスクリプションです。"
        
    try:
        # APIキーを取得
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            logger.warning("OPENROUTER_API_KEY environment variable is not set")
            raise ValueError("OPENROUTER_API_KEY is required")

        # プロンプトテンプレートを作成
        prompt_template_str = """
        # 指示
        以下の内容をもとにメタディスクリプションを生成してください。

        # メタディスクリプションの要件
        - 主題や主要ポイントを簡潔に要約する
        - SEOを意識し、検索結果で魅力的に見える説明にする
        - 120文字以内で作成する
        - 文末に「...」を付けない
        - キーワードを自然に含める
        - VRChatやコミュニティ名、発表者名など固有名詞があれば自然に含める
        - 読者が思わずクリックしたくなるような表現を心がける

        # 禁止事項
        - 120文字を超えてはいけない
        - 「記事」「ブログ」という単語を使わない
        - 「まとめ」「解説」という単語を使わない
        - 「！」「？」などの記号は使わない
        - 開催日時などは含めない

        # 入力テキスト
        {text}

        # 出力
        メタディスクリプションだけを出力してください。他の余計なテキストは含めないでください。
        """
        prompt = prompt_template_str.format(text=text)

        # OpenAI SDKを使用してOpenRouterにリクエスト
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key
        )
        
        # ヘッダーを追加してリクエスト
        completion = client.chat.completions.create(
            extra_headers={
                "HTTP-Referer": "https://vrc-ta-hub.com/",  # OpenRouterのランキング用サイトURL
                "X-Title": "VRC TA Hub"  # OpenRouterのランキング用サイト名
            },
            model=model,
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=300
        )
        
        # レスポンスからテキストを取得
        meta_description = completion.choices[0].message.content.strip()

        # 250文字の制限は維持 (ただし、プロンプトでは120文字を指示)
        # 念のため、モデルが指示を守らなかった場合を考慮してトリミング
        if len(meta_description) > 250:
            logger.warning(f"Generated meta description exceeded 250 characters ({len(meta_description)}). Trimming.")
            meta_description = meta_description[:250]

        return meta_description

    except Exception as e:
        logger.warning(f"メタディスクリプションの生成中にエラーが発生しました: {str(e)}")
        if "API key" in str(e):
            logger.error("OPENROUTER_API_KEY environment variable might be missing or invalid.")
        
        # エラーが発生した場合は従来の方法でメタディスクリプションを生成 (フォールバック)
        lines = text.split('\n')
        lines = [line.strip() for line in lines if line.strip()]
        if lines and (lines[0].startswith('# ') or lines[0].startswith('#')):
            lines = lines[1:]
        content = ' '.join([
            line.replace('## ', '').replace('### ', '').replace('#### ', '')
            for line in lines
            if not line.startswith('>')
        ])
        # フォールバックの長さ制限も厳密にする
        max_fallback_len = 120
        if len(content) > max_fallback_len:
            # 日本語を考慮し、単純な文字数でカット（'...'は含めない）
            content = content[:max_fallback_len]
        return content.strip()
