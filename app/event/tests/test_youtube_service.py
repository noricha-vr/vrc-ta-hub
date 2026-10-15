from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from event.services import youtube_service


class GetTranscriptTest(SimpleTestCase):
    """youtube-transcript-api 1.x の呼び方（インスタンスの list / 断片の .text）で字幕を結合する。"""

    def _youtube(self):
        youtube = MagicMock()
        youtube.videos.return_value.list.return_value.execute.return_value = {"items": [{"id": "vid"}]}
        return youtube

    @patch.object(youtube_service, "build")
    @patch.object(youtube_service, "YouTubeTranscriptApi")
    def test_joins_japanese_snippets(self, api_class, build):
        build.return_value = self._youtube()
        transcript = MagicMock()
        transcript.fetch.return_value = [SimpleNamespace(text="はい"), SimpleNamespace(text="始めます")]
        api_class.return_value.list.return_value.find_transcript.return_value = transcript

        self.assertEqual(youtube_service.get_transcript("vid"), "はい\n始めます")
        api_class.return_value.list.assert_called_once_with("vid")
        api_class.return_value.list.return_value.find_transcript.assert_called_once_with(["ja"])

    @patch.object(youtube_service, "build")
    @patch.object(youtube_service, "YouTubeTranscriptApi")
    def test_translates_english_when_no_japanese(self, api_class, build):
        build.return_value = self._youtube()
        transcript_list = api_class.return_value.list.return_value
        english = MagicMock()
        english.translate.return_value.fetch.return_value = [SimpleNamespace(text="こんにちは")]

        def find(languages):
            if languages == ["ja"]:
                raise youtube_service.NoTranscriptFound("vid", ["ja"], transcript_list)
            return english

        transcript_list.find_transcript.side_effect = find

        self.assertEqual(youtube_service.get_transcript("vid"), "こんにちは")
        english.translate.assert_called_once_with("ja")

    @patch.object(youtube_service, "build")
    @patch.object(youtube_service, "YouTubeTranscriptApi")
    def test_disabled_transcripts_are_none(self, api_class, build):
        build.return_value = self._youtube()
        api_class.return_value.list.side_effect = youtube_service.TranscriptsDisabled("vid")

        self.assertIsNone(youtube_service.get_transcript("vid"))
