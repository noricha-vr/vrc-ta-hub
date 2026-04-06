import importlib
import json

from django.test import TestCase, override_settings
from django.urls import reverse

from api_v1.serializers import GatheringListSchemaSerializer


class SchemaAPITest(TestCase):
    def test_gathering_list_schema_serializer_exposes_fields_for_openapi(self):
        serializer = GatheringListSchemaSerializer()

        self.assertIn('ジャンル', serializer.fields)
        self.assertIn('ポスター転載可', serializer.fields)
        self.assertEqual(serializer.fields['ポスター転載可'].__class__.__name__, 'BooleanField')

    def test_schema_endpoint_includes_gathering_list_response_schema(self):
        response = self.client.get(f"{reverse('schema')}?format=json")

        self.assertEqual(response.status_code, 200)

        payload = json.loads(response.content)
        response_schema = payload['paths']['/api/v1/community/gathering-list/']['get']['responses']['200']
        item_schema = response_schema['content']['application/json']['schema']['items']
        self.assertEqual(item_schema['$ref'], '#/components/schemas/GatheringList')

        gathering_list_schema = payload['components']['schemas']['GatheringList']
        properties = gathering_list_schema['properties']

        self.assertEqual(gathering_list_schema['type'], 'object')
        self.assertIn('ジャンル', properties)
        self.assertIn('ポスター転載可', properties)
        self.assertEqual(properties['ポスター転載可']['type'], 'boolean')

    def test_schema_endpoint_returns_valid_openapi_json(self):
        """スキーマエンドポイントが有効な OpenAPI JSON を返すことを確認する。"""
        response = self.client.get(f"{reverse('schema')}?format=json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/vnd.oai.openapi+json')

        payload = json.loads(response.content)
        self.assertEqual(payload['openapi'], '3.0.3')
        self.assertEqual(payload['info']['title'], 'VRC技術学術系Hub API')
        self.assertEqual(payload['info']['version'], '1.0.0')

    def test_schema_endpoint_contains_expected_paths(self):
        """スキーマが全 API パスを含んでいることを確認する。"""
        response = self.client.get(f"{reverse('schema')}?format=json")
        payload = json.loads(response.content)
        paths = payload['paths']

        expected_paths = [
            '/api/v1/community/',
            '/api/v1/event/',
            '/api/v1/event_detail/',
        ]
        for expected_path in expected_paths:
            self.assertIn(expected_path, paths, f"{expected_path} がスキーマに含まれていない")

    def test_schema_endpoint_always_accessible(self):
        """スキーマエンドポイントは DEBUG に関係なく常にアクセス可能であることを確認する。

        テスト環境は DEBUG=False で動作するため、これ自体が本番相当の確認になる。
        """
        response = self.client.get(f"{reverse('schema')}?format=json")

        self.assertEqual(response.status_code, 200)


def _get_url_names_for_debug(debug_value):
    """指定した DEBUG 値で website.urls を再読み込みし、登録された URL 名を返す。"""
    import website.urls
    with override_settings(DEBUG=debug_value):
        importlib.reload(website.urls)
        names = {
            p.name for p in website.urls.urlpatterns
            if hasattr(p, 'name') and p.name
        }
    return names


class SwaggerUIAccessControlTest(TestCase):
    """Swagger UI / ReDoc が DEBUG=True の時のみ公開される URL 構成をテストする。"""

    def tearDown(self):
        """テスト後に元の状態に戻す。"""
        import website.urls
        importlib.reload(website.urls)

    def test_docs_urls_included_when_debug_true(self):
        """DEBUG=True の場合に swagger-ui と redoc が urlpatterns に含まれることを検証する。"""
        url_names = _get_url_names_for_debug(True)

        self.assertIn('swagger-ui', url_names)
        self.assertIn('redoc', url_names)
        self.assertIn('schema', url_names)

    def test_docs_urls_excluded_when_debug_false(self):
        """DEBUG=False の場合に swagger-ui と redoc が urlpatterns に含まれないことを検証する。"""
        url_names = _get_url_names_for_debug(False)

        self.assertNotIn('swagger-ui', url_names)
        self.assertNotIn('redoc', url_names)
        # schema は常に含まれる
        self.assertIn('schema', url_names)

    def test_docs_endpoints_not_accessible_in_test_environment(self):
        """テスト環境（DEBUG=False）では /api/docs/ と /api/redoc/ が 404 を返すことを確認する。"""
        response = self.client.get('/api/docs/')
        self.assertEqual(response.status_code, 404)

        response = self.client.get('/api/redoc/')
        self.assertEqual(response.status_code, 404)
