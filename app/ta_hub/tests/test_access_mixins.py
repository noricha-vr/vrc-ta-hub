from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import URLPattern, URLResolver, get_resolver
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

    def test_urlconf_user_passes_views_do_not_use_default_permission_denial(self):
        """URLConf の権限ビューが PermissionDenied 経路に戻らないことを保証する."""
        risky_views = []

        def walk(patterns, route_prefix=""):
            for pattern in patterns:
                if isinstance(pattern, URLResolver):
                    walk(pattern.url_patterns, route_prefix + str(pattern.pattern))
                    continue
                if not isinstance(pattern, URLPattern):
                    continue

                view_class = getattr(pattern.callback, "view_class", None)
                if not view_class or not issubclass(view_class, UserPassesTestMixin):
                    continue

                handler_owner = view_class.handle_no_permission.__qualname__
                if handler_owner.startswith("AccessMixin."):
                    risky_views.append(
                        f"{route_prefix}{pattern.pattern} -> "
                        f"{view_class.__module__}.{view_class.__name__}"
                    )

        walk(get_resolver().url_patterns)

        self.assertEqual(risky_views, [])
