import logging
import os
import tempfile

from django.core.files import File
from django.test import TestCase

from account.models import CustomUser
from community.models import Community
from event.libs import generate_blog, get_transcript, BlogOutput
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
        result = generate_blog(event_detail)

        # BlogOutputモデルの検証
        self.assertIsInstance(result, BlogOutput)
        # 内容の存在確認
        self.assertTrue(result.title)
        self.assertTrue(result.meta_description)
        self.assertTrue(result.text)

    def test_generate_blog_video_only(self):
        event_detail = self.create_event_detail(
            youtube_url="https://www.youtube.com/watch?v=rrKl0s23E0M"
        )
        result = generate_blog(event_detail)

        self.assertIsInstance(result, BlogOutput)
        self.assertTrue(result.title)
        self.assertTrue(result.meta_description)
        self.assertTrue(result.text)

    def test_generate_blog_pdf_only(self):
        event_detail = self.create_event_detail(slide_file=True)
        result = generate_blog(event_detail)

        self.assertIsInstance(result, BlogOutput)
        self.assertTrue(result.title)
        self.assertTrue(result.meta_description)
        self.assertTrue(result.text)

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

    def test_get_transcript(self):
        # テスト実行
        video_id = "ewqOnvr8tAU"
        result = get_transcript(video_id)
        self.assertIsNotNone(result)
        self.assertGreater(len(result), 0)
        logger.info(result)
