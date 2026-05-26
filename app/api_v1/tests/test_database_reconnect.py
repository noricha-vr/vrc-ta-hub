from unittest.mock import patch

from django.db.utils import OperationalError
from django.test import SimpleTestCase
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from api_v1.base import DatabaseReconnectListMixin
from api_v1.views import (
    CommunityViewSet,
    EventDetailAPIViewSet,
    EventDetailViewSet,
    EventViewSet,
    RecurrenceRuleViewSet,
)


class AuthenticatedUser:
    is_authenticated = True
    is_superuser = True
    pk = 1


class TransientFailureListViewSet(viewsets.ViewSet):
    def list(self, request):
        if not hasattr(self, 'attempts'):
            self.attempts = 0
        self.attempts += 1
        if self.attempts == 1:
            raise OperationalError(2013, 'Lost connection to server during query')
        return Response({'attempts': self.attempts})


class PersistentConnectFailureListViewSet(viewsets.ViewSet):
    def list(self, request):
        raise OperationalError(2002, "Can't connect to server on 'mysql.example.test' (115)")


class HandshakeFailureListViewSet(viewsets.ViewSet):
    def list(self, request):
        raise OperationalError(
            2013,
            "Lost connection to server at 'handshake: reading initial communication packet'",
        )


class NonRetryableFailureListViewSet(viewsets.ViewSet):
    def list(self, request):
        raise OperationalError(1064, 'SQL syntax error')


class RetryingListViewSet(DatabaseReconnectListMixin, TransientFailureListViewSet):
    pass


class PersistentConnectFailureRetryingListViewSet(
    DatabaseReconnectListMixin,
    PersistentConnectFailureListViewSet,
):
    pass


class HandshakeFailureRetryingListViewSet(DatabaseReconnectListMixin, HandshakeFailureListViewSet):
    pass


class NonRetryableListViewSet(DatabaseReconnectListMixin, NonRetryableFailureListViewSet):
    pass


class DatabaseReconnectListMixinTest(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def test_list_retries_once_after_mysql_lost_connection(self):
        view = RetryingListViewSet.as_view({'get': 'list'})
        request = self.factory.get('/api/v1/community/')

        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'attempts': 2})

    def test_list_returns_503_when_mysql_connect_failure_persists(self):
        view = PersistentConnectFailureRetryingListViewSet.as_view({'get': 'list'})
        request = self.factory.get('/api/v1/community/')

        response = view(request)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data, {'detail': 'Database temporarily unavailable.'})

    def test_list_returns_503_when_mysql_handshake_failure_persists(self):
        view = HandshakeFailureRetryingListViewSet.as_view({'get': 'list'})
        request = self.factory.get('/api/v1/community/')

        response = view(request)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data, {'detail': 'Database temporarily unavailable.'})

    def test_list_does_not_retry_non_disconnect_operational_error(self):
        view = NonRetryableListViewSet.as_view({'get': 'list'})
        request = self.factory.get('/api/v1/community/')

        with self.assertRaises(OperationalError):
            view(request)

    def test_all_api_v1_viewsets_retry_list_after_mysql_disconnect(self):
        target_viewsets = (
            CommunityViewSet,
            EventViewSet,
            EventDetailViewSet,
            EventDetailAPIViewSet,
            RecurrenceRuleViewSet,
        )

        for viewset_class in target_viewsets:
            with self.subTest(viewset=viewset_class.__name__):
                view = viewset_class.as_view({'get': 'list'})
                request = self.factory.get('/api/v1/test/')
                force_authenticate(request, user=AuthenticatedUser())

                with (
                    patch('api_v1.base.connections.close_all') as mock_close_all,
                    patch('rest_framework.mixins.ListModelMixin.list') as mock_list,
                ):
                    mock_list.side_effect = [
                        OperationalError(2013, 'Lost connection to server during query'),
                        Response({'viewset': viewset_class.__name__}),
                    ]

                    response = view(request)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, {'viewset': viewset_class.__name__})
                self.assertEqual(mock_list.call_count, 2)
                mock_close_all.assert_called_once_with()
