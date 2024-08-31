import logging
import os

import google.generativeai as genai
from django.core.files import File
from django.test import TestCase

from account.models import CustomUser
from community.models import Community
from event.libs import generate_blog
from event.models import Event
from event.models import EventDetail

logger = logging.getLogger(__name__)

genai.configure(api_key=os.environ["GEMINI_API_KEY"])


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

        # ローカルファイルのパスを設定
        local_file_path = os.path.join('event', 'tests', 'input_data', 'perplexity.pdf')

        # ファイルが存在することを確認
        if not os.path.exists(local_file_path):
            raise FileNotFoundError(f"File not found: {local_file_path}")

        # EventDetailオブジェクトを作成し、ローカルファイルを設定
        with open(local_file_path, 'rb') as file:
            cls.event_detail = EventDetail.objects.create(
                theme="Perplexityってどうなのよ？",
                speaker="のりちゃん",
                event=cls.event,
                youtube_url="https://www.youtube.com/watch?v=rrKl0s23E0M",
                slide_file=File(file, name='perplexity.pdf')
            )

    def test_generate_blog(self):
        # トランスクリプトの取得（モック化することをお勧めします）
        text = generate_blog(self.event_detail)
        self.assertGreater(len(text), 100)
