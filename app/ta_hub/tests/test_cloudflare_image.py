
from django.template import Context, Template
from django.test import TestCase, override_settings

from ta_hub.libs import cloudflare_image_url


class CloudflareImageUrlTest(TestCase):
    """cloudflare_image_url ユーティリティ関数のテスト。"""

    @override_settings(AWS_S3_CUSTOM_DOMAIN='data.vrc-ta-hub.com')
    def test_r2_url_is_transformed(self):
        url = 'https://data.vrc-ta-hub.com/poster/1/abc123.jpg'
        result = cloudflare_image_url(url, width=400)
        self.assertEqual(
            result,
            'https://data.vrc-ta-hub.com/cdn-cgi/image/width=400,quality=80,format=auto/poster/1/abc123.jpg'
        )

    @override_settings(AWS_S3_CUSTOM_DOMAIN='data.vrc-ta-hub.com')
    def test_custom_quality_and_format(self):
        url = 'https://data.vrc-ta-hub.com/poster/1/abc123.jpg'
        result = cloudflare_image_url(url, width=800, quality=90, format='webp')
        self.assertEqual(
            result,
            'https://data.vrc-ta-hub.com/cdn-cgi/image/width=800,quality=90,format=webp/poster/1/abc123.jpg'
        )

    @override_settings(AWS_S3_CUSTOM_DOMAIN='data.vrc-ta-hub.com')
    def test_already_transformed_url_is_not_double_transformed(self):
        url = 'https://data.vrc-ta-hub.com/cdn-cgi/image/width=400,quality=80,format=auto/poster/1/abc123.jpg'
        result = cloudflare_image_url(url, width=800)
        self.assertEqual(result, url)

    @override_settings(AWS_S3_CUSTOM_DOMAIN=None)
    def test_no_custom_domain_returns_original(self):
        url = '/media/poster/1/abc123.jpg'
        result = cloudflare_image_url(url, width=400)
        self.assertEqual(result, url)

    @override_settings(AWS_S3_CUSTOM_DOMAIN='data.vrc-ta-hub.com')
    def test_local_url_is_not_transformed(self):
        url = '/media/poster/1/abc123.jpg'
        result = cloudflare_image_url(url, width=400)
        self.assertEqual(result, url)

    @override_settings(AWS_S3_CUSTOM_DOMAIN='data.vrc-ta-hub.com')
    def test_different_domain_is_not_transformed(self):
        url = 'https://example.com/poster/1/abc123.jpg'
        result = cloudflare_image_url(url, width=400)
        self.assertEqual(result, url)

    def test_empty_url_returns_empty(self):
        self.assertEqual(cloudflare_image_url('', width=400), '')
        self.assertIsNone(cloudflare_image_url(None, width=400))


class CfResizeTemplateFilterTest(TestCase):
    """cf_resize テンプレートフィルタのテスト。"""

    @override_settings(AWS_S3_CUSTOM_DOMAIN='data.vrc-ta-hub.com')
    def test_filter_in_template(self):
        template = Template('{% load image_tags %}{{ url|cf_resize:"400" }}')
        context = Context({'url': 'https://data.vrc-ta-hub.com/poster/1/abc.jpg'})
        result = template.render(context)
        self.assertIn('/cdn-cgi/image/width=400', result)

    @override_settings(AWS_S3_CUSTOM_DOMAIN='data.vrc-ta-hub.com')
    def test_filter_with_invalid_width(self):
        template = Template('{% load image_tags %}{{ url|cf_resize:"abc" }}')
        context = Context({'url': 'https://data.vrc-ta-hub.com/poster/1/abc.jpg'})
        result = template.render(context)
        # 不正な width の場合は元 URL がそのまま返る
        self.assertEqual(result.strip(), 'https://data.vrc-ta-hub.com/poster/1/abc.jpg')
