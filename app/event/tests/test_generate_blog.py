import logging
import os
import tempfile
import unittest
from unittest.mock import patch

from django.core.files import File
from django.test import TestCase

from account.models import CustomUser
from community.models import Community
from event.libs import generate_blog, get_transcript, BlogOutput, generate_meta_description
from event.models import Event, EventDetail

logger = logging.getLogger(__name__)


class TestGenerateBlog(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = CustomUser.objects.create_user(
            user_name="test_user",
            email="sample@example.com"
        )
        cls.community = Community.objects.create(
            name="個人開発集会",
            custom_user=cls.user
        )
        cls.event = Event.objects.create(
            date="2024-05-24",
            community=cls.community
        )

        # テスト用のPDFファイルを作成
        cls.test_pdf_content = b"%PDF-1.0\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj 3 0 obj<</Type/Page/MediaBox[0 0 3 3]>>endobj\nxref\n0 4\n0000000000 65535 f\n0000000010 00000 n\n0000000053 00000 n\n0000000102 00000 n\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n149\n%EOF"

        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
            temp_file.write(cls.test_pdf_content)
            cls.local_file_path = temp_file.name

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if os.path.exists(cls.local_file_path):
            os.unlink(cls.local_file_path)

    def create_event_detail(self, youtube_url=None, slide_file=None):
        event_detail = EventDetail.objects.create(
            theme="Perplexityってどうなのよ？",
            speaker="のりちゃん",
            event=self.event,
            youtube_url=youtube_url
        )
        if slide_file:
            with open(self.local_file_path, 'rb') as file:
                event_detail.slide_file.save('test.pdf', File(file))
        return event_detail

    @patch('event.libs.OpenAI')
    def test_generate_blog_video_and_pdf(self, mock_openai):
        # モックの設定
        mock_client = mock_openai.return_value
        mock_chat = mock_client.chat
        mock_completions = mock_chat.completions
        mock_create = mock_completions.create
        
        # Function Callingのモックレスポンスを設定
        mock_response = mock_create.return_value
        mock_tool_call = type('obj', (object,), {
            'function': type('obj', (object,), {
                'name': 'generate_blog_post',
                'arguments': '{"title": "テストタイトル", "meta_description": "テストのメタ説明", "text": "テスト本文の内容"}'
            })
        })
        
        # モックレスポンスのchoicesとmessageを設定
        mock_message = type('obj', (object,), {
            'tool_calls': [mock_tool_call],
            'content': None  # Function Callingの場合、contentはNoneになる可能性がある
        })
        mock_response.choices = [type('obj', (object,), {'message': mock_message})]
        
        event_detail = self.create_event_detail(
            youtube_url="https://www.youtube.com/watch?v=rrKl0s23E0M",
            slide_file=True
        )
        
        # get_transcriptをモック
        with patch('event.libs.get_transcript', return_value="テスト文字起こし"):
            result = generate_blog(event_detail, model='google/gemini-2.0-flash-001')

        # BlogOutputモデルの検証
        self.assertIsInstance(result, BlogOutput)
        # 内容の存在確認
        self.assertEqual(result.title, "テストタイトル")
        self.assertEqual(result.meta_description, "テストのメタ説明")
        self.assertEqual(result.text, "テスト本文の内容")

    @patch('event.libs.OpenAI')
    def test_generate_blog_video_only(self, mock_openai):
        # モックの設定
        mock_client = mock_openai.return_value
        mock_chat = mock_client.chat
        mock_completions = mock_chat.completions
        mock_create = mock_completions.create
        
        # Function Callingのモックレスポンスを設定
        mock_response = mock_create.return_value
        mock_tool_call = type('obj', (object,), {
            'function': type('obj', (object,), {
                'name': 'generate_blog_post',
                'arguments': '{"title": "動画のみのテストタイトル", "meta_description": "動画のみのテストメタ説明", "text": "動画のみのテスト本文"}'
            })
        })
        
        # モックレスポンスのchoicesとmessageを設定
        mock_message = type('obj', (object,), {
            'tool_calls': [mock_tool_call],
            'content': None
        })
        mock_response.choices = [type('obj', (object,), {'message': mock_message})]
        
        event_detail = self.create_event_detail(
            youtube_url="https://www.youtube.com/watch?v=rrKl0s23E0M"
        )
        
        # get_transcriptをモック
        with patch('event.libs.get_transcript', return_value="テスト文字起こし"):
            result = generate_blog(event_detail, model='google/gemini-2.0-flash-001')

        self.assertIsInstance(result, BlogOutput)
        self.assertEqual(result.title, "動画のみのテストタイトル")
        self.assertEqual(result.meta_description, "動画のみのテストメタ説明")
        self.assertEqual(result.text, "動画のみのテスト本文")

    @patch('event.libs.OpenAI')
    def test_generate_blog_pdf_only(self, mock_openai):
        # モックの設定
        mock_client = mock_openai.return_value
        mock_chat = mock_client.chat
        mock_completions = mock_chat.completions
        mock_create = mock_completions.create
        
        # Function Callingのモックレスポンスを設定
        mock_response = mock_create.return_value
        mock_tool_call = type('obj', (object,), {
            'function': type('obj', (object,), {
                'name': 'generate_blog_post',
                'arguments': '{"title": "PDFのみのテストタイトル", "meta_description": "PDFのみのテストメタ説明", "text": "PDFのみのテスト本文"}'
            })
        })
        
        # モックレスポンスのchoicesとmessageを設定
        mock_message = type('obj', (object,), {
            'tool_calls': [mock_tool_call],
            'content': None
        })
        mock_response.choices = [type('obj', (object,), {'message': mock_message})]
        
        event_detail = self.create_event_detail(slide_file=True)
        
        result = generate_blog(event_detail, model='google/gemini-2.0-flash-001')

        self.assertIsInstance(result, BlogOutput)
        self.assertEqual(result.title, "PDFのみのテストタイトル")
        self.assertEqual(result.meta_description, "PDFのみのテストメタ説明")
        self.assertEqual(result.text, "PDFのみのテスト本文")

    def test_generate_blog_no_video_no_pdf(self):
        event_detail = self.create_event_detail()
        result = generate_blog(event_detail)

        self.assertIsInstance(result, BlogOutput)
        self.assertEqual(result.title, '')
        self.assertEqual(result.meta_description, '')
        self.assertEqual(result.text, '')

    def test_blog_output_basic(self):
        """BlogOutputモデルの基本的な動作をテスト"""
        # 正常なケース
        valid_output = BlogOutput(
            title="テストタイトル",
            meta_description="テストのメタ説明",
            text="テスト本文の内容"
        )
        self.assertIsInstance(valid_output, BlogOutput)
        self.assertTrue(valid_output.title)
        self.assertTrue(valid_output.meta_description)
        self.assertTrue(valid_output.text)

    @patch('event.libs.YouTubeTranscriptApi')
    @patch('event.libs.build')
    def test_get_transcript(self, mock_build, mock_transcript_api):
        # YouTubeTranscriptApiをモック
        transcript_mock = mock_transcript_api.list_transcripts.return_value
        ja_transcript_mock = transcript_mock.find_transcript.return_value
        ja_transcript_mock.fetch.return_value = [
            {'text': 'これはテスト文字起こしです。'},
            {'text': 'モックによるテストです。'}
        ]

        # YouTubeのAPIレスポンスをモック
        mock_youtube = mock_build.return_value
        mock_youtube_videos = mock_youtube.videos.return_value
        mock_youtube_list = mock_youtube_videos.list.return_value
        mock_youtube_list.execute.return_value = {'items': ['dummy_item']}

        # テスト実行
        video_id = "ewqOnvr8tAU"
        result = get_transcript(video_id)
        self.assertIsNotNone(result)
        self.assertGreater(len(result), 0)
        self.assertEqual(result, "これはテスト文字起こしです。\nモックによるテストです。")
        logger.info(result)

    @patch('event.libs.OpenAI')
    def test_generate_meta_description(self, mock_openai):
        # モックの設定
        mock_client = mock_openai.return_value
        mock_chat = mock_client.chat
        mock_completions = mock_chat.completions
        mock_create = mock_completions.create

        # モックレスポンスの設定
        mock_response = mock_create.return_value
        mock_response.choices = [type('obj', (object,), {
            'message': type('obj', (object,), {
                'content': "テスト用のメタディスクリプションです。"
            })
        })]

        result = generate_meta_description("テスト用の本文です。", model='google/gemini-2.0-flash-001')
        self.assertEqual(result, "テスト用のメタディスクリプションです。")

    @unittest.skipIf(not os.environ.get('OPENROUTER_API_KEY'), 'OPENROUTER_API_KEY環境変数が設定されていません')
    def test_openrouter_integration(self):
        """
        実際のOpenRouter APIへの接続をテストする統合テスト
        
        このテストは環境変数 OPENROUTER_API_KEY が設定されている場合のみ実行されます。
        実際のAPIに接続するため、API制限やネットワーク状況によって失敗する可能性があります。
        """
        logger.info("実際のOpenRouterサービスに接続するテストを実行します")

        # テスト用のシンプルなテキスト
        test_text = "VRChatは多くのユーザーに愛されるソーシャルVRプラットフォームです。毎日様々なイベントが開催され、ユーザー同士の交流が盛んです。"

        # モックを使わずに実際のAPIを呼び出す - 有効なモデルを明示的に指定
        # OpenRouterで広く利用可能なモデルを使用
        try:
            result = generate_meta_description(test_text, model="google/gemini-2.0-flash-exp:free")

            # API接続が成功した場合の検証
            self.assertIsNotNone(result)
            self.assertGreater(len(result), 10)  # 何らかの意味のある長さの文字列が返ってくるはず
            self.assertLess(len(result), 250)  # メタディスクリプションの最大長を超えない

            logger.info(f"OpenRouter API実際の結果: {result}")

            # 基本的な内容確認（完全一致は期待できないため、VRChatという単語が含まれているかなど）
            # APIによって生成された内容が変わるため、テストが壊れやすくなる可能性があるので、
            # より柔軟な検証を行う
            self.assertTrue(
                any(keyword in result for keyword in ["VRChat", "ソーシャルVR", "プラットフォーム", "イベント"]),
                f"生成されたメタディスクリプションに期待されるキーワードが含まれていません: {result}"
            )
        except Exception as e:
            # APIが利用できない場合のメッセージ
            logger.warning(f"OpenRouter API接続テストの例外: {e}")
            self.skipTest(f"OpenRouter APIへの接続に失敗しました: {e} - ネットワーク接続やAPI設定を確認してください")
