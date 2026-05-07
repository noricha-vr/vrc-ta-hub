from django.db.utils import OperationalError
from django.test import SimpleTestCase
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory

from api_v1.views import DatabaseReconnectListMixin


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
