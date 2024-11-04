import logging
import os
import tempfile

from django.core.files import File
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from account.models import CustomUser
from community.models import Community
from event.libs import generate_blog, upload_file_to_gemini
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

    def test_generate_blog_video_and_pdf(self):
        event_detail = self.create_event_detail(
            youtube_url="https://www.youtube.com/watch?v=rrKl0s23E0M",
            slide_file=True
        )
        text = generate_blog(event_detail)
        self.assertGreater(len(text), 100)

    def test_generate_blog_video_only(self):
        event_detail = self.create_event_detail(
            youtube_url="https://www.youtube.com/watch?v=rrKl0s23E0M"
        )
        text = generate_blog(event_detail)
        self.assertGreater(len(text), 100)

    def test_generate_blog_pdf_only(self):
        event_detail = self.create_event_detail(slide_file=True)
        text = generate_blog(event_detail)
        self.assertGreater(len(text), 100)

    def test_generate_blog_no_video_no_pdf(self):
        event_detail = self.create_event_detail()
        text = generate_blog(event_detail)
        self.assertEqual(len(text), 0)

    def test_upload_file_to_gemini_success(self):
        event_detail = self.create_event_detail(slide_file=True)
        result = upload_file_to_gemini(event_detail)
        self.assertIsNotNone(result)
        self.assertTrue(hasattr(result, 'name'))

    def test_upload_file_to_gemini_no_file(self):
        event_detail = self.create_event_detail()
        with self.assertRaises(ValueError):
            upload_file_to_gemini(event_detail)

    def test_upload_file_to_gemini_file_handling(self):
        dummy_file = SimpleUploadedFile("test.pdf", self.test_pdf_content, content_type="application/pdf")
        event_detail = EventDetail.objects.create(
            theme="Test Theme",
            speaker="Test Speaker",
            event=self.event,
            slide_file=dummy_file
        )
        result = upload_file_to_gemini(event_detail)
        self.assertIsNotNone(result)
        self.assertTrue(hasattr(result, 'name'))

    def test_fetch_transcription(self):
        from googleapiclient.discovery import build
        from youtube_transcript_api import YouTubeTranscriptApi

        def get_video_info_and_captions(video_id) -> str:
            try:
                # YouTube APIクライアントを構築
                API_KEY = os.environ.get("GOOGLE_API_KEY")
                # APIキーを設定
                assert API_KEY is not None, "APIキーが設定されていません"
                youtube = build('youtube', 'v3', developerKey=API_KEY)

                # 動画の詳細情報を取得
                video_response = youtube.videos().list(
                    part='snippet',
                    id=video_id
                ).execute()

                if not video_response['items']:
                    raise ValueError('動画が見つかりませんでした')

                video_title = video_response['items'][0]['snippet']['title']

                # 字幕を取得 (認証不要)
                transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

                # 日本語字幕を優先的に取得し、なければ英語字幕を取得して翻訳
                try:
                    transcript = transcript_list.find_transcript(['ja'])
                except:
                    transcript = transcript_list.find_transcript(['en']).translate('ja')

                # 字幕テキストを結合
                captions_text = "\n".join([entry['text'] for entry in transcript.fetch()])

                return captions_text

            except Exception as e:
                logger.error(f"Youtubeから文字起こしを取得するときにエラーが発生しました: {str(e)}")
                return None

        # テスト実行
        video_id = "ewqOnvr8tAU"
        result = get_video_info_and_captions(video_id)
        self.assertIsNotNone(result)
        self.assertGreater(len(result), 0)
        logger.info(result)
