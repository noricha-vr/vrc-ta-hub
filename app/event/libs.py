import logging
import os
import tempfile
import uuid

import bleach
import google.generativeai as genai
import markdown
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter

from event.models import EventDetail
from website.settings import GEMINI_API_KEY

genai.configure(api_key=GEMINI_API_KEY)
genai_model = genai.GenerativeModel('gemini-1.5-pro-exp-0801')
logger = logging.getLogger(__name__)

genai.configure(api_key=os.environ["GEMINI_API_KEY"])


def generate_blog(event_detail: EventDetail, model='gemini-1.5-flash') -> str:
    """
    EventDetailに関連付けられたスライドファイルをもとにブログ記事を生成する関数

    Args:
        event_detail (EventDetail): ブログ記事を生成するための情報を含むEventDetailオブジェクト

    Returns:
        str: 生成されたブログ記事
    """
    # youtube か slide file がない場合は処理を終了
    if not event_detail.youtube_url and not event_detail.slide_file:
        return ''
    # YouTube動画から文字起こしを取得
    genai_model = genai.GenerativeModel(model)
    transcript = get_transcript(event_detail.video_id, "ja")
    prompt = create_blog_prompt(event_detail, transcript)
    uploaded_file = None
    try:
        upload_file_to_gemini(event_detail)
        response = genai_model.generate_content([prompt, uploaded_file], stream=False)
    except Exception as e:
        logger.warning(f"Error uploading file to Gemini for EventDetail {event_detail.pk}: {e}")
        response = genai_model.generate_content(prompt, stream=False)
    logger.info('text: ' + response.text)
    return response.text


def upload_file_to_gemini(event_detail: EventDetail) -> genai.types.File:
    """
    EventDetailに関連付けられたスライドファイルをGemini APIにアップロードする関数

    Args:
        event_detail (EventDetail): アップロードするファイルを含むEventDetailオブジェクト

    Returns:
        genai.types.File: アップロードされたファイルオブジェクト

    Raises:
        ValueError: スライドファイルが存在しない場合
        Exception: アップロード中に発生したその他のエラー
    """
    if not event_detail.slide_file:
        raise ValueError("No slide file associated with this EventDetail")

    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
        event_detail.slide_file.seek(0)
        temp_file.write(event_detail.slide_file.read())
        temp_file_path = temp_file.name

    try:
        uploaded_file = genai.upload_file(
            path=temp_file_path,
            name=str(uuid.uuid4()),
            mime_type='application/pdf'
        )
        return uploaded_file
    except Exception as e:
        logger.error(f"Error uploading file to Gemini for EventDetail {event_detail.pk}: {e}")
        raise
    finally:
        os.unlink(temp_file_path)


def get_transcript(video_id, language='ja') -> str:
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
    transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=[language])
    formatter = TextFormatter()
    transcript_text = formatter.format_transcript(transcript)
    return transcript_text


def create_blog_prompt(event_detail: EventDetail, transcript: str) -> str:
    prompt = f"""
    ## 文字起こし内容
    {transcript}
    
    ## 指示
    {event_detail.event.date}にVRChatの「{event_detail.event.community.name}」で行われた{event_detail.speaker}の発表内容をもとに、[文字起こし内容]とその時に使われたPDF（スライド）情報を使ってブログ記事を作成します。
    発表のテーマは「{event_detail.theme}」です。
    
    ## 制御
    - マークダウン形式で出力
    - 1行目 h1(#)でタイトルを出力
    - 2行目以降は h2 h3 h4 にあたる見出しや、リスト、テーブルを使って読者にわかりやすくまとめる
    - 文字起こしは精度が低いためテーマ、前後の文脈から名詞や単語を補ってブログを作成する
    - 文章の流れが自然になるように見出しと内容の連携を強化
    - 発表者の敬称がない場合は「さん」をつける
    - 記事の冒頭に発表のハイライトや重要なポイントをh2で短く示す
    - タイトルや見出し、記事内で発表テーマに関連するキーワードを適宜使用し、SEOを意識する
    - 最後にまとめをつける
    - 文字起こしされたプレゼンテーションのPDFファイルは記事内に埋め込む
    - 最低1000文字以上の記事を目指す
    - ポップさ 80%、フォーマルさ 20%で文章を作成する
    """

    return prompt


def convert_markdown(markdown_text: str) -> str:
    """MarkdownをHTMLに変換し、サニタイズする"""
    allowed_tags = ['a', 'p', 'h1', 'h2', 'h3', 'h4', 'ul', 'ol', 'li', 'strong', 'em', 'code', 'pre', 'table', 'thead',
                    'tbody', 'tr', 'th', 'td', 'hr', 'br']
    allowed_attributes = {'a': ['href', 'title'], 'pre': ['class'], 'table': ['class']}
    html = markdown.markdown(markdown_text, extensions=['tables'])

    # BeautifulSoupを使ってHTMLをパース
    soup = BeautifulSoup(html, 'html.parser')

    # テーブルタグにクラスを追加
    for table in soup.find_all('table'):
        table['class'] = table.get('class', []) + ['table', 'table-responsive']

    # パース後のHTMLを文字列に変換
    html = str(soup)

    return bleach.clean(html, tags=allowed_tags, attributes=allowed_attributes)
