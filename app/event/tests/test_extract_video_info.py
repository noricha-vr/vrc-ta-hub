"""extract_video_id と extract_video_info 関数のテスト"""

from django.test import TestCase

from event.views import extract_video_id, extract_video_info, _parse_youtube_time


class TestExtractVideoId(TestCase):
    """extract_video_id関数のテスト"""

    def test_standard_youtube_url(self):
        """標準的なYouTube URLからvideo_idを抽出"""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(extract_video_id(url), "dQw4w9WgXcQ")

    def test_short_youtube_url(self):
        """短縮YouTube URLからvideo_idを抽出"""
        url = "https://youtu.be/dQw4w9WgXcQ"
        self.assertEqual(extract_video_id(url), "dQw4w9WgXcQ")

    def test_youtube_url_with_timestamp(self):
        """タイムスタンプ付きURLからvideo_idを抽出"""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ?t=123"
        self.assertEqual(extract_video_id(url), "dQw4w9WgXcQ")

    def test_youtube_url_with_timestamp_ampersand(self):
        """&t=形式のタイムスタンプ付きURLからvideo_idを抽出"""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=123"
        self.assertEqual(extract_video_id(url), "dQw4w9WgXcQ")

    def test_empty_url(self):
        """空のURLはNoneを返す"""
        self.assertIsNone(extract_video_id(""))
        self.assertIsNone(extract_video_id(None))

    def test_invalid_url(self):
        """無効なURLはNoneを返す"""
        self.assertIsNone(extract_video_id("https://example.com"))


class TestParseYoutubeTime(TestCase):
    """_parse_youtube_time関数のテスト"""

    def test_numeric_seconds(self):
        """純粋な数値（秒）のパース"""
        self.assertEqual(_parse_youtube_time("123"), 123)
        self.assertEqual(_parse_youtube_time("0"), 0)
        self.assertEqual(_parse_youtube_time("3600"), 3600)

    def test_minutes_and_seconds(self):
        """分秒形式（1m30s）のパース"""
        self.assertEqual(_parse_youtube_time("1m30s"), 90)
        self.assertEqual(_parse_youtube_time("2m45s"), 165)

    def test_minutes_only(self):
        """分のみ（2m）のパース"""
        self.assertEqual(_parse_youtube_time("2m"), 120)
        self.assertEqual(_parse_youtube_time("10m"), 600)

    def test_seconds_only_with_suffix(self):
        """秒のみ（90s）のパース"""
        self.assertEqual(_parse_youtube_time("90s"), 90)
        self.assertEqual(_parse_youtube_time("30s"), 30)


class TestExtractVideoInfo(TestCase):
    """extract_video_info関数のテスト"""

    def test_url_without_timestamp(self):
        """タイムスタンプなしのURL"""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        video_id, start_time = extract_video_info(url)
        self.assertEqual(video_id, "dQw4w9WgXcQ")
        self.assertIsNone(start_time)

    def test_url_with_numeric_timestamp_question(self):
        """?t=123形式のタイムスタンプ"""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ?t=123"
        video_id, start_time = extract_video_info(url)
        self.assertEqual(video_id, "dQw4w9WgXcQ")
        self.assertEqual(start_time, 123)

    def test_url_with_numeric_timestamp_ampersand(self):
        """&t=123形式のタイムスタンプ"""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=456"
        video_id, start_time = extract_video_info(url)
        self.assertEqual(video_id, "dQw4w9WgXcQ")
        self.assertEqual(start_time, 456)

    def test_url_with_minutes_seconds_timestamp(self):
        """?t=1m30s形式のタイムスタンプ"""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ?t=1m30s"
        video_id, start_time = extract_video_info(url)
        self.assertEqual(video_id, "dQw4w9WgXcQ")
        self.assertEqual(start_time, 90)

    def test_url_with_minutes_only_timestamp(self):
        """?t=2m形式のタイムスタンプ"""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ?t=2m"
        video_id, start_time = extract_video_info(url)
        self.assertEqual(video_id, "dQw4w9WgXcQ")
        self.assertEqual(start_time, 120)

    def test_short_url_with_timestamp(self):
        """短縮URLのタイムスタンプ"""
        url = "https://youtu.be/dQw4w9WgXcQ?t=90"
        video_id, start_time = extract_video_info(url)
        self.assertEqual(video_id, "dQw4w9WgXcQ")
        self.assertEqual(start_time, 90)

    def test_empty_url(self):
        """空のURL"""
        video_id, start_time = extract_video_info("")
        self.assertIsNone(video_id)
        self.assertIsNone(start_time)

        video_id, start_time = extract_video_info(None)
        self.assertIsNone(video_id)
        self.assertIsNone(start_time)

    def test_real_world_url(self):
        """問題で報告されていた実際のURL形式"""
        url = "https://www.youtube.com/watch?v=rrKl0s23E0M?t=123"
        video_id, start_time = extract_video_info(url)
        self.assertEqual(video_id, "rrKl0s23E0M")
        self.assertEqual(start_time, 123)
