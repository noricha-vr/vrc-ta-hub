"""実 SDK と HTTPX2 を通して OpenRouter 互換の入出力を確かめる。外部通信なし。"""

import json
from unittest.mock import patch

import httpx2
from django.test import TestCase
from openai import OpenAI

from event.services.content_generation_service import BlogSources, generate_blog
from tests.factories import make_community, make_event, make_event_detail
from website.constants import OPENROUTER_BASE_URL


class OpenAISdkCompatibilityTest(TestCase):
    def setUp(self):
        self.detail = make_event_detail(
            make_event(make_community()), youtube_url='https://www.youtube.com/watch?v=abcdefghijk',
        )
        self.payload = {'title': 'タイトル', 'meta_description': '要約', 'text': '記事の本文'}
        self.requests = []

    def _client(self, message):
        def respond(request):
            self.requests.append(request)
            return httpx2.Response(200, json={
                'id': 'chatcmpl-test', 'object': 'chat.completion', 'created': 0,
                'model': 'test-model',
                'choices': [{'index': 0, 'finish_reason': 'stop', 'message': message}],
            })

        client = OpenAI(
            base_url=OPENROUTER_BASE_URL, api_key='offline-test-key', max_retries=0,
            http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
        )
        self.addCleanup(client.close)
        return client

    def _generate(self, message):
        with (
            patch.dict('os.environ', {'OPENROUTER_API_KEY': 'offline-test-key'}),
            patch('event.services.content_generation_service.OpenAI', return_value=self._client(message)),
        ):
            return generate_blog(
                self.detail, model='test-model', sources=BlogSources(transcript='字幕', pdf_content='', pdf_url=''),
            )

    def test_function_call_uses_real_sdk_model_and_serialization(self):
        result = self._generate({
            'role': 'assistant', 'content': None,
            'tool_calls': [{
                'id': 'call-test', 'type': 'function',
                'function': {'name': 'generate_blog_post', 'arguments': json.dumps(self.payload)},
            }],
        })
        self.assertEqual(result.model_dump(), self.payload)
        request = self.requests[0]
        self.assertEqual(request.url.path, '/api/v1/chat/completions')
        self.assertEqual(request.headers['Authorization'], 'Bearer offline-test-key')
        body = json.loads(request.content)
        self.assertEqual(body['model'], 'test-model')
        self.assertEqual(body['tool_choice']['function']['name'], 'generate_blog_post')

    def test_custom_call_falls_back_to_json_content(self):
        result = self._generate({
            'role': 'assistant', 'content': json.dumps(self.payload),
            'tool_calls': [{
                'id': 'call-test', 'type': 'custom',
                'custom': {'name': 'other_tool', 'input': 'not blog JSON'},
            }],
        })
        self.assertEqual(result.model_dump(), self.payload)

    def test_custom_call_without_content_returns_empty_output(self):
        with self.assertLogs('event.services.content_generation_service', level='ERROR') as logs:
            result = self._generate({
                'role': 'assistant', 'content': None,
                'tool_calls': [{
                    'id': 'call-test', 'type': 'custom',
                    'custom': {'name': 'other_tool', 'input': 'not blog JSON'},
                }],
            })
        self.assertEqual(result.title, '')
        self.assertTrue(any('neither tool_calls nor content' in entry for entry in logs.output))

    def test_plain_content_still_generates_blog(self):
        result = self._generate({'role': 'assistant', 'content': json.dumps(self.payload)})
        self.assertEqual(result.model_dump(), self.payload)
