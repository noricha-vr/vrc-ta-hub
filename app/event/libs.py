import logging
import os
import tempfile
from typing import Optional

import bleach
import markdown
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from langchain.output_parsers import ResponseSchema, StructuredOutputParser
from langchain.prompts import PromptTemplate, ChatPromptTemplate, HumanMessagePromptTemplate
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from youtube_transcript_api import YouTubeTranscriptApi
from langchain.chains import LLMChain
from langchain.output_parsers import PydanticOutputParser
from langchain_core.pydantic_v1 import validator

from event.models import EventDetail
from website.settings import GEMINI_API_KEY, GOOGLE_API_KEY

logger = logging.getLogger(__name__)


class BlogOutput(BaseModel):
    """ブログ記事の出力形式を定義するPydanticモデル"""
    title: str = Field(description="ブログ記事のタイトル。SEOを意識した40文字以内の魅力的なタイトル。")
    meta_description: str = Field(description="ブログ記事のメタディスクリプション。120文字以内でコンテンツの要約を記述。")
    text: str = Field(description="ブログ記事の本文。マークダウン形式で記述された1000〜1800文字の記事。")

    @validator('title')
    def validate_title_length(cls, v):
        if len(v) > 40:
            raise ValueError('タイトルは40文字以内である必要があります')
        return v

    @validator('meta_description')
    def validate_meta_description_length(cls, v):
        if len(v) > 120:
            raise ValueError('メタディスクリプションは120文字以内である必要があります')
        return v

    @validator('text')
    def validate_text_length(cls, v):
        text_length = len(v)
        if not (1000 <= text_length <= 1800):
            raise ValueError('本文は1000〜1800文字の間である必要があります')
        return v


def generate_blog(event_detail: EventDetail, model='gemini-2.0-flash-exp') -> BlogOutput:
    """
    EventDetailに関連付けられたスライドファイルをもとにブログ記事を生成する関数

    Args:
        event_detail (EventDetail): ブログ記事を生成するための情報を含むEventDetailオブジェクト
        model (str): 使用するGeminiモデル名

    Returns:
        BlogOutput: タイトル、メタディスクリプション、本文を含むPydanticモデル
    """
    # youtube か slide file がない場合は処理を終了
    if not event_detail.youtube_url and not event_detail.slide_file:
        return BlogOutput(title='', meta_description='', text='')

    # Pydantic出力パーサーを設定
    parser = PydanticOutputParser(pydantic_object=BlogOutput)

    # Langchainのモデルを初期化
    llm = ChatGoogleGenerativeAI(
        model=model,
        google_api_key=GEMINI_API_KEY,
        temperature=0.7,
    )

    # YouTube動画から文字起こしを取得
    logger.info(f"Gemini model: {model}")
    transcript = get_transcript(event_detail.video_id, "ja")

    # PDFの内容を取得
    pdf_content = ""
    if event_detail.slide_file:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
            event_detail.slide_file.seek(0)
            temp_file.write(event_detail.slide_file.read())
            temp_file_path = temp_file.name

        try:
            loader = PyPDFLoader(temp_file_path)
            pages = loader.load()
            pdf_content = "\n".join([page.page_content for page in pages])
        except Exception as e:
            logger.warning(f"Error loading PDF for EventDetail {event_detail.pk}: {e}")
        finally:
            os.unlink(temp_file_path)

    # プロンプトテンプレートを作成
    prompt = PromptTemplate(
        template="""
    # 文字起こし内容
    {transcript}

    # PDFの内容
    {pdf_content}

    # 指示
    {date}にVRChatの「{community_name}」で行われた{speaker}の発表内容をもとに、[文字起こし内容]とその時に使われたPDF（スライド）情報を使ってブログ記事を作成します。
    発表のテーマは「{theme}」です。

    # ブログ記事の作成指示
    - 文字起こしされた文章とプレゼンテーションで使用されたスライド（PDFファイル）を使ってブログ記事を作成
    - 文字起こしは精度が低いためテーマやスライドから前後の文脈や名詞、単語を補うこと
    - 文章の流れが自然になるように見出しと内容の連携を強化
    - SEOを意識して、タイトル、見出しを設定し、文中にキーワードを盛り込む
    - 発表を聞いた人の視点で、発表を聞けなかった人のためにわかりやすくまとめる

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
    - 積極的に空行を追加することで、読みやすさを重視
    - PDFがあればスライドのダウンロードリンクを含める

    # 禁止事項
    - PDFの内容を画像として記事に埋めこんではいけません
    - 参考文献を出力してはいけません
    - h2の前にインデックス番号をつけてはいけません
    - h2を最大7個以上作ってはいけません
    - 硬い文章表現を控えてください
    
    # 禁止ワード
    - ブログ

    {format_instructions}
    """,
        input_variables=["transcript", "pdf_content", "date", "community_name", "speaker", "theme"],
        partial_variables={"format_instructions": parser.get_format_instructions()}
    )

    # チェーンを作成
    chain = LLMChain(llm=llm, prompt=prompt)

    try:
        # チェーンを実行してコンテンツを生成
        output = chain.run(
            transcript=transcript or "",
            pdf_content=pdf_content,
            date=event_detail.event.date,
            community_name=event_detail.event.community.name,
            speaker=event_detail.speaker,
            theme=event_detail.theme
        )
        
        # 出力をパース
        blog_output = parser.parse(output)
        logger.info('Generated content: ' + str(blog_output))
        return blog_output

    except Exception as e:
        logger.warning(f"Error generating content for EventDetail {event_detail.pk}: {e}")
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


def generate_meta_description(text: str) -> str:
    """
    Geminiを使用してブログ記事のテキストからメタディスクリプションを生成する関数

    Args:
        text (str): ブログ記事の全文テキスト

    Returns:
        str: 生成されたメタディスクリプション（120文字以内）
    """
    try:
        # Langchainのモデルを初期化
        llm = ChatGoogleGenerativeAI(
            model='gemini-1.0-pro',
            google_api_key=GEMINI_API_KEY,
            temperature=0.3,
        )

        # プロンプトテンプレートを作成
        prompt_template = PromptTemplate.from_template("""
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
        """)

        # プロンプトを生成
        prompt = prompt_template.format(text=text)

        # LangChainを使用してメタディスクリプションを生成
        response = llm.invoke([HumanMessage(content=prompt)])
        meta_description = response.content.strip()

        return meta_description[:250]

    except Exception as e:
        logger.warning(f"メタディスクリプションの生成中にエラーが発生しました: {str(e)}")
        # エラーが発生した場合は従来の方法でメタディスクリプションを生成
        lines = text.split('\n')
        lines = [line.strip() for line in lines if line.strip()]
        if lines and (lines[0].startswith('# ') or lines[0].startswith('#')):
            lines = lines[1:]
        content = ' '.join([
            line.replace('## ', '').replace('### ', '').replace('#### ', '')
            for line in lines
            if not line.startswith('>')
        ])
        if len(content) > 120:
            content = content[:117] + '...'
        return content.strip()
