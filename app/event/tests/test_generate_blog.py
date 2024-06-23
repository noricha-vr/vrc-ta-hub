import logging
import os
import time

import google.generativeai as genai
from django.test import TestCase

from account.models import CustomUser
from community.models import Community
from event.libs import get_transcript, create_blog_prompt
from event.models import Event, EventDetail

logger = logging.getLogger(__name__)

genai.configure(api_key=os.environ["GEMINI_API_KEY"])


class TestGenerateBlog(TestCase):
    def test_generate_blog_from_video(self):
        # Test the function generate_blog
        # Create a test blog
        video_id = "rrKl0s23E0M"
        transcript = get_transcript(video_id, "ja")
        event_detail = EventDetail.objects.create(
            theme="個人開発のススメ",
            speaker="のりちゃん",
            event=Event.objects.create(
                date="2022-01-01",
                community=Community.objects.create(
                    name="個人開発集会",
                    custom_user=CustomUser.objects.create_user(
                        user_name="個人開発集会",
                        email="sample@example.com"
                    )
                )
            )
        )
        prompt = create_blog_prompt(event_detail, transcript)
        genai_model = genai.GenerativeModel('gemini-1.5-flash')
        response = genai_model.generate_content(prompt, stream=False)
        logger.info(response.text)
        self.assertGreater(len(response.text), 100)

    def test_generate_blog_from_ppt(self):
        # Test the function generate_blog
        # Create a test blog
        """
        Install the Google AI Python SDK

        $ pip install google-generativeai

        See the getting started guide for more information:
        https://ai.google.dev/gemini-api/docs/get-started/python
        """

        def upload_to_gemini(path, mime_type=None):
            """Uploads the given file to Gemini.

            See https://ai.google.dev/gemini-api/docs/prompting_with_media
            """
            file = genai.upload_file(path, mime_type=mime_type)
            print(f"Uploaded file '{file.display_name}' as: {file.uri}")
            return file

        def wait_for_files_active(files):
            """Waits for the given files to be active.

            Some files uploaded to the Gemini API need to be processed before they can be
            used as prompt inputs. The status can be seen by querying the file's "state"
            field.

            This implementation uses a simple blocking polling loop. Production code
            should probably employ a more sophisticated approach.
            """
            print("Waiting for file processing...")
            for name in (file.name for file in files):
                file = genai.get_file(name)
                while file.state.name == "PROCESSING":
                    print(".", end="", flush=True)
                    time.sleep(10)
                    file = genai.get_file(name)
                if file.state.name != "ACTIVE":
                    raise Exception(f"File {file.name} failed to process")
            print("...all files ready")
            print()

        # Create the model
        # See https://ai.google.dev/api/python/google/generativeai/GenerativeModel
        generation_config = {
            "temperature": 1,
            "top_p": 0.95,
            "top_k": 64,
            "max_output_tokens": 8192,
            "response_mime_type": "text/plain",
        }

        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config=generation_config,
            # safety_settings = Adjust safety settings
            # See https://ai.google.dev/gemini-api/docs/safety-settings
        )

        # TODO Make these files available on the local file system
        # You may need to update the file paths
        files = [
            upload_to_gemini("event/tests/input_data/2024425.pdf", mime_type="application/pdf"),
        ]

        # Some files have a processing delay. Wait for them to be ready.
        wait_for_files_active(files)

        chat_session = model.start_chat(
            history=[
                {
                    "role": "user",
                    "parts": [
                        files[0],
                        "# 役割\nスライドの内容を下にブログ記事を作成します。",
                        "\n    ## 制御\n    - マークダウン形式で出力\n    - h1 h2 h3 h4 に当たるタイトルや見出し、リストを使って読者にわかりやすくまとめる\n    - 文字起こしは精度が低いためテーマ、前後の文脈から名詞や単語を補ってブログを作成する\n    - 文章の流れが自然になるように見出しと内容の連携を強化\n    - 発表者の敬称がない場合は「さん」をつける\n    - 記事の冒頭に発表のハイライトや重要なポイントをh2で短く示す\n    - 記事内で発表テーマに関連するキーワードを適宜使用し、SEOを意識\n    - 最後にまとめをつける\n    - 最低1000文字以上の記事を目指す",
                    ],
                },
            ]
        )

        response = chat_session.send_message("INSERT_INPUT_HERE")

        print(response.text)
