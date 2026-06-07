from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.views import View

from ta_hub.access_mixins import AuthenticatedForbiddenMixin


User = get_user_model()


class _DeniedView(LoginRequiredMixin, AuthenticatedForbiddenMixin, View):
    def test_func(self):
        return False

    def get(self, request):
        return HttpResponse('ok')


class _AllowedView(LoginRequiredMixin, AuthenticatedForbiddenMixin, View):
    def test_func(self):
        return True

    def get(self, request):
        return HttpResponse('ok')


class AuthenticatedForbiddenMixinTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            user_name='normal_user',
            email='normal@example.com',
            password='testpass123',
        )

    def test_authenticated_failure_returns_403_response(self):
        request = self.factory.get('/private/')
        request.user = self.user

        response = _DeniedView.as_view()(request)

        self.assertEqual(response.status_code, 403)

    def test_anonymous_failure_redirects_to_login(self):
        request = self.factory.get('/private/')
        request.user = AnonymousUser()

        response = _DeniedView.as_view()(request)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/account/login/', response.url)

    def test_authenticated_success_continues_to_view(self):
        request = self.factory.get('/private/')
        request.user = self.user

        response = _AllowedView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')
