import logging
import os
import tempfile
import uuid
from typing import Optional

import bleach
import google.generativeai as genai
import markdown
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi

from event.models import EventDetail
from website.settings import GEMINI_API_KEY, GOOGLE_API_KEY

logger = logging.getLogger(__name__)
genai.configure(api_key=GEMINI_API_KEY)


def generate_blog(event_detail: EventDetail, model='gemini-2.0-flash-exp') -> str:
    """
    EventDetailに関連付けられたスライドファイルをもとにブログ記事を生成する関数

    Args:
        event_detail (EventDetail): ブログ記事を生成するための情報を含むEventDetailオブジェクト
        model (str): 使用するGeminiモデル名

    Returns:
        str: 生成されたブログ記事
    """
    # youtube か slide file がない場合は処理を終了
    if not event_detail.youtube_url and not event_detail.slide_file:
        return ''
    # YouTube動画から文字起こしを取得
    logger.info(f"Gemini model: {model}")
    genai_model = genai.GenerativeModel(model)
    transcript = get_transcript(event_detail.video_id, "ja")
    prompt = create_blog_prompt(event_detail, transcript)
    try:
        if event_detail.slide_file:
            uploaded_file = upload_file_to_gemini(event_detail)
            response = genai_model.generate_content([prompt, uploaded_file], stream=False)
        else:
            response = genai_model.generate_content(prompt, stream=False)
    except Exception as e:
        logger.warning(f"Error generating content for EventDetail {event_detail.pk}: {e}")
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


def create_blog_prompt(event_detail: EventDetail, transcript: str) -> str:
    prompt = f"""
    # 文字起こし内容
    {transcript}

    """

    if event_detail.slide_file:
        prompt += f"## PDFのURL：{event_detail.slide_file.url}\n\n"

    prompt += f"""
    # 指示
    {event_detail.event.date}にVRChatの「{event_detail.event.community.name}」で行われた{event_detail.speaker}の発表内容をもとに、[文字起こし内容]とその時に使われたPDF（スライド）情報を使ってブログ記事を作成します。
    発表のテーマは「{event_detail.theme}」です。

    # ブログ記事の作成指示
    - 文字起こしされた文章とプレゼンテーションで使用されたスライド（PDFファイル）を使ってブログ記事を作成
    - 文字起こしは精度が低いためテーマやスライドから前後の文脈や名詞、単語を補うこと
    - 文章の流れが自然になるように見出しと内容の連携を強化
    - SEOを意識して、タイトル、見出しを設定し、文中にキーワードを盛り込む

    # フォーマット
    - マークダウン形式で出力
    - 1行目 h1(#)でタイトルを出力
    - 2行目以降は h2 h3 h4 にあたる見出しや、リスト、テーブルを使って読者にわかりやすくまとめる
    - h3 h4を積極的に活用する
    - 記事の冒頭に発表のハイライトや重要なポイントをh2で短く示す
    - 発表者の敬称がない場合は「さん」をつける
    - 最後にまとめをつける
    - まとめの最後にはスライドのリンクを貼る
    - コンテンツは1000〜1800文字に制限
    - ポップさ 80%、フォーマルさ 20%で文章を作成する

    # 禁止事項
    - PDFの内容を画像として記事に埋めこんではいけません
    - 参考文献を出力してはいけません
    - h2の前にインデックス番号をつけてはいけません
    - h2を最大7個以上作ってはいけません
    - 硬い文章表現を控えてください
    """

    return prompt


def convert_markdown(markdown_text: str) -> str:
    """MarkdownをHTMLに変換し、サニタイズする"""
    allowed_tags = ['a', 'p', 'h1', 'h2', 'h3', 'h4', 'ul', 'ol', 'li', 'strong', 'em', 'code', 'pre', 'table', 'thead',
                    'tbody', 'tr', 'th', 'td', 'hr', 'br', 'blockquote']
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
