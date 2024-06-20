import bleach
import google.generativeai as genai
import markdown
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter

from event.models import EventDetail
from website.settings import GEMINI_API_KEY

# Gemini APIの設定
genai.configure(api_key=GEMINI_API_KEY)
genai_model = genai.GenerativeModel('gemini-1.5-pro')


def get_transcript(video_id, language='ja') -> str:
    """
    YouTube動画から文字起こしを取得する関数

    Args:
      video_id: YouTube動画のID
      language: 文字起こしの言語のリスト。デフォルトは日本語

    Returns:
      文字起こしテキスト
    """
    transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=[language])
    formatter = TextFormatter()
    transcript_text = formatter.format_transcript(transcript)
    return transcript_text


def create_blog_prompt(event_detail: EventDetail) -> str:
    prompt = f"""
    # 指示
    VRChatの「{event_detail.event.community.name}」で{event_detail.speaker}の発表内容をもとに、ユーザーから入力された文字起こしデータを使ってブログ記事を作成します。
    発表のテーマは「{event_detail.theme}」です。
    # 制御
    - マークダウン形式
    - タイトル不要
    - 本文はh2 h3に当たる見出しやリストを使ってわかりやすくまとめる
    - 文字起こしは精度が低いためテーマ、前後の文脈から名詞や単語を補ってブログを作成する
    - 最後にまとめをつける
    """
    return prompt


def convert_markdown(markdown_text: str) -> str:
    """MarkdownをHTMLに変換し、サニタイズする"""
    allowed_tags = ['a', 'p', 'h1', 'h2', 'h3', 'h4', 'ul', 'ol', 'li', 'strong', 'em', 'code', 'pre', 'table', 'thead',
                    'tbody', 'tr', 'th', 'td']
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
