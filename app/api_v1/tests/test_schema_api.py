import json

from django.test import TestCase
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
