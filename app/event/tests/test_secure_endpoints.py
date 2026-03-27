from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse


class SecureEndpointErrorResponseTest(TestCase):
    def test_sync_calendar_events_hides_internal_error_details(self):
        with patch('event.views.DatabaseToGoogleSync') as mock_sync:
            mock_sync.return_value.sync_all_communities.side_effect = RuntimeError(
                'database password leaked'
            )

            response = self.client.get(
                reverse('event:sync_calendar_events'),
                HTTP_REQUEST_TOKEN=settings.REQUEST_TOKEN,
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.content.decode(),
            'Failed to sync calendar events.',
        )
        self.assertNotIn('database password leaked', response.content.decode())

    def test_generate_llm_events_hides_internal_error_details(self):
        with patch('event.views_llm_generate.call_command') as mock_call_command:
            mock_call_command.side_effect = RuntimeError('traceback details')

            response = self.client.get(
                reverse('event:generate_llm_events'),
                HTTP_REQUEST_TOKEN=settings.REQUEST_TOKEN,
            )

        self.assertEqual(response.status_code, 500)
        self.assertJSONEqual(
            response.content,
            {
                'status': 'error',
                'message': 'LLM event generation failed.',
                'timestamp': response.json()['timestamp'],
            },
        )
        self.assertNotIn('traceback details', response.content.decode())
