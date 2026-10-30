"""Misskey の API 契約・安全な画像取得・外部呼び出し遮断のテスト。"""

import base64
import json
from urllib.parse import quote
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, override_settings

from twitter.image_download import MAX_IMAGE_SIZE
from twitter.misskey_api import _post_api, post_note, upload_file

ENV = {
    'MISSKEY_TOKEN': 'test-misskey-token',
    'MISSKEY_HOST': 'misskey.io',
    'MISSKEY_VISIBILITY': 'public',
    'MISSKEY_API_ALLOW_TEST_CALLS': '1',
}
IMAGE_URL = 'https://data.vrc-ta-hub.com/poster.png'
PNG = b'\x89PNG\r\n\x1a\nimage'


def api_response(data, status=200, body='{}'):
    """実通信せず、requests の応答を再現する。"""
    response = MagicMock(status_code=status, text=body)
    response.json.return_value = data
    return response


@override_settings(TESTING=True)
@patch.dict('os.environ', ENV)
class MisskeyApiTest(SimpleTestCase):
    def setUp(self):
        self.post = patch('twitter.misskey_api.requests.post').start()
        self.get = patch('twitter.image_download.requests.get').start()
        self.addCleanup(patch.stopall)
        self.post.return_value = api_response({'createdNote': {'id': 'note-123'}})
        self.get.return_value = api_response({})
        self.get.return_value.iter_content.return_value = [PNG]
        self.get.return_value.headers = {'Content-Type': 'image/png'}

    def test_note_payload_defaults(self):
        result = post_note('告知 #VRChat')
        self.assertTrue(result['ok'])
        self.assertEqual(result['data']['id'], 'note-123')
        self.assertEqual(result['status_code'], 200)
        self.post.assert_called_once_with(
            'https://misskey.io/api/notes/create',
            json={'i': ENV['MISSKEY_TOKEN'], 'text': '告知 #VRChat',
                  'visibility': 'public', 'noExtractMentions': True},
            timeout=30, allow_redirects=False,
        )
        self.get.assert_not_called()

    @patch.dict('os.environ', {'MISSKEY_HOST': 'social.example.com', 'MISSKEY_VISIBILITY': 'home'})
    def test_host_and_visibility_are_configurable(self):
        post_note('告知')
        self.assertEqual(self.post.call_args.args[0], 'https://social.example.com/api/notes/create')
        self.assertEqual(self.post.call_args.kwargs['json']['visibility'], 'home')

    def test_image_is_multipart_uploaded_and_file_id_attached(self):
        self.post.side_effect = [
            api_response({'id': 'file-1'}), api_response({'createdNote': {'id': 'note-1'}}),
        ]
        result = post_note('画像付き告知', image_url=IMAGE_URL)
        self.assertTrue(result['ok'])
        upload, note = self.post.call_args_list
        self.assertEqual(upload.args[0], 'https://misskey.io/api/drive/files/create')
        self.assertEqual(upload.kwargs['data'], {'i': ENV['MISSKEY_TOKEN']})
        self.assertEqual(upload.kwargs['files'], {'file': ('image', PNG, 'image/png')})
        self.assertEqual(note.kwargs['json']['fileIds'], ['file-1'])
        self.get.return_value.close.assert_called_once()

    def test_upload_returns_same_result_shape(self):
        self.post.return_value = api_response({'id': 'file-1'})
        self.assertEqual(upload_file(IMAGE_URL), {
            'ok': True, 'data': {'id': 'file-1'}, 'status_code': 200, 'error_body': None,
        })

    @patch.dict('os.environ', {'MISSKEY_TOKEN': ''})
    def test_missing_token_never_calls_http(self):
        self.assertFalse(post_note('告知', IMAGE_URL)['ok'])
        self.assertFalse(upload_file(IMAGE_URL)['ok'])
        self.get.assert_not_called()
        self.post.assert_not_called()

    @patch.dict('os.environ', {'MISSKEY_API_ALLOW_TEST_CALLS': ''})
    def test_test_guard_blocks_all_http_by_default(self):
        self.assertFalse(post_note('告知', IMAGE_URL)['ok'])
        self.assertFalse(upload_file(IMAGE_URL)['ok'])
        self.get.assert_not_called()
        self.post.assert_not_called()

    @override_settings(TESTING=False)
    @patch.dict('os.environ', {'MISSKEY_API_ALLOW_TEST_CALLS': ''})
    def test_runner_detection_blocks_images_and_both_post_endpoints(self):
        for argv in (
            ['manage.py', 'test', 'twitter'], ['pytest', 'twitter'],
            ['/venv/bin/pytest', 'twitter'], ['python', '-m', 'pytest', 'twitter'],
            ['/venv/bin/py.test', 'twitter'],
        ):
            with self.subTest(argv=argv), patch('sys.argv', argv):
                self.assertFalse(post_note('告知', IMAGE_URL)['ok'])
                self.assertFalse(upload_file(IMAGE_URL)['ok'])
                self.assertFalse(_post_api('notes/create', payload={'text': '告知'})['ok'])
                self.assertFalse(_post_api('drive/files/create', files={'file': b'image'})['ok'])
        self.get.assert_not_called()
        self.post.assert_not_called()

    @override_settings(TESTING=False)
    @patch('sys.argv', ['manage.py', 'test', 'twitter'])
    def test_explicit_test_allowance_still_permits_mocked_calls(self):
        self.post.side_effect = [api_response({'id': 'file-1'}),
                                 api_response({'createdNote': {'id': 'note-1'}})]
        self.assertTrue(post_note('告知', IMAGE_URL)['ok'])
        self.get.assert_called_once()
        self.assertEqual(self.post.call_count, 2)

    def test_text_limit_is_validated_before_upload(self):
        for text in ('', 'あ' * 3001):
            with self.subTest(length=len(text)):
                self.assertFalse(post_note(text, IMAGE_URL)['ok'])
        self.get.assert_not_called()
        self.post.assert_not_called()
        self.assertTrue(post_note('あ' * 3000)['ok'])

    @patch.dict('os.environ', {'MISSKEY_VISIBILITY': 'typo'})
    def test_invalid_visibility_is_rejected(self):
        self.assertFalse(post_note('告知')['ok'])
        self.post.assert_not_called()

    @patch.dict('os.environ', {'MISSKEY_HOST': 'misskey.io/path?i=unsafe'})
    def test_invalid_host_is_rejected(self):
        self.assertFalse(post_note('告知')['ok'])
        self.post.assert_not_called()

    def test_http_failure_keeps_only_status_and_allowed_error_code(self):
        body = '先頭' + 'あ' * 500 + 'LOG_TAIL'
        response = api_response({'error': {'code': 'RATE_LIMIT_EXCEEDED', 'message': body}},
                                status=429, body=body)
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        self.post.return_value = response
        with self.assertLogs('twitter.misskey_api', level='ERROR') as logs:
            result = post_note('告知')
        self.assertFalse(result['ok'])
        self.assertEqual(result['status_code'], 429)
        self.assertEqual(result['error_body'], 'RATE_LIMIT_EXCEEDED')
        self.assertIn('RATE_LIMIT_EXCEEDED', ''.join(logs.output))
        self.assertNotIn('先頭', str(result) + ''.join(logs.output))
        self.assertNotIn('LOG_TAIL', ''.join(logs.output))
        self.assertNotIn('あ' * 67, ''.join(logs.output))

    def test_error_bodies_and_encoded_tokens_are_never_forwarded(self):
        token = ENV['MISSKEY_TOKEN']
        bodies = [
            token, token.replace('-', r'\u002d'), quote(token, safe='').replace('-', '%2D'),
            base64.b64encode(token.encode()).decode(), json.dumps(token.split('-')),
        ]
        for body in bodies:
            for data in ({'error': {'code': 'ACCESS_DENIED', 'message': body}},
                         {'error': {'code': body}}, {'error': {'code': ['ACCESS_DENIED']}}, {}):
                with self.subTest(body=body, data=data):
                    response = api_response(data, status=403, body=body)
                    response.raise_for_status.side_effect = requests.HTTPError(response=response)
                    self.post.return_value = response
                    with self.assertLogs('twitter.misskey_api', level='ERROR') as logs:
                        result = post_note('告知')
                    expected = 'ACCESS_DENIED' if data.get('error', {}).get('code') == 'ACCESS_DENIED' else 'UNKNOWN_ERROR'
                    self.assertEqual(result['error_body'], expected)
                    self.assertEqual(result['status_code'], 403)
                    self.assertNotIn(body, str(result) + ''.join(logs.output))

    def test_non_json_error_response_does_not_forward_body_or_parser_exception(self):
        response = api_response({}, status=502, body='secret response')
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        response.json.side_effect = ValueError('secret parser error')
        self.post.return_value = response
        with self.assertLogs('twitter.misskey_api', level='ERROR') as logs:
            result = post_note('告知')
        self.assertEqual(result['error_body'], 'UNKNOWN_ERROR')
        self.assertNotIn('secret', str(result) + ''.join(logs.output))

    def test_error_response_does_not_expose_token(self):
        response = api_response({}, status=403, body=f"bad token {ENV['MISSKEY_TOKEN']}")
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        self.post.return_value = response
        with self.assertLogs('twitter.misskey_api', level='ERROR') as logs:
            result = post_note('告知')
        self.assertNotIn(ENV['MISSKEY_TOKEN'], result['error_body'])
        self.assertNotIn(ENV['MISSKEY_TOKEN'], ''.join(logs.output))

    def test_timeout_is_a_failure_result(self):
        self.post.side_effect = requests.Timeout('timeout')
        self.assertFalse(post_note('告知')['ok'])

    def test_malformed_success_response_is_a_failure(self):
        for data in ({}, {'createdNote': {}}, {'createdNote': []}, [], None):
            with self.subTest(data=data):
                self.post.return_value = api_response(data)
                self.assertFalse(post_note('告知')['ok'])
        self.post.return_value = api_response({})
        self.post.return_value.json.side_effect = ValueError('bad json')
        self.assertFalse(post_note('告知')['ok'])

    def test_redirect_response_is_not_treated_as_success(self):
        self.post.return_value = api_response({'createdNote': {'id': 'wrong'}}, status=302)
        self.assertFalse(post_note('告知')['ok'])

    def test_upload_failure_does_not_create_text_only_note(self):
        response = api_response({}, status=500)
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        self.post.return_value = response
        self.assertFalse(post_note('告知', IMAGE_URL)['ok'])
        self.post.assert_called_once()
        self.assertIn('drive/files/create', self.post.call_args.args[0])

    def test_untrusted_image_domain_is_rejected(self):
        self.assertFalse(post_note('告知', 'https://evil.example/image.png')['ok'])
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_image_size_limit_and_magic_bytes_are_enforced(self):
        for data in (PNG + b'x' * MAX_IMAGE_SIZE, b'<html>not an image</html>', b''):
            with self.subTest(size=len(data)):
                self.get.return_value.iter_content.return_value = [data]
                self.assertFalse(post_note('告知', IMAGE_URL)['ok'])
        self.post.assert_not_called()
        self.assertEqual(self.get.return_value.close.call_count, 3)

    def test_download_redirect_is_rejected(self):
        self.get.return_value.status_code = 302
        self.assertFalse(post_note('告知', IMAGE_URL)['ok'])
        self.assertFalse(self.get.call_args.kwargs['allow_redirects'])
        self.post.assert_not_called()

    def test_image_at_size_limit_is_allowed(self):
        self.get.return_value.iter_content.return_value = [PNG + b'x' * (MAX_IMAGE_SIZE - len(PNG))]
        self.post.return_value = api_response({'id': 'file-1'})
        self.assertTrue(upload_file(IMAGE_URL)['ok'])

    def test_image_download_exception_is_a_failure(self):
        self.get.side_effect = requests.Timeout('timeout')
        self.assertFalse(post_note('告知', IMAGE_URL)['ok'])
        self.post.assert_not_called()
