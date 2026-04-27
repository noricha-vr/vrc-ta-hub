"""
WSGI config for website project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

from website.middleware import CanonicalCloudRunHostMiddleware

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')


class CloudRunHostCanonicalizingWSGIApplication:
    """Django request 生成前に Cloud Run preview host を正規化する。"""

    def __init__(self, django_application):
        self.django_application = django_application
        self.host_middleware = CanonicalCloudRunHostMiddleware(lambda request: request)

    def __call__(self, environ, start_response):
        # CommonMiddleware は早い段階で request.get_host() を呼ぶため、
        # Django の middleware chain に入る前に WSGI environ を揃える。
        self.host_middleware.canonicalize_host_mapping(environ)

        return self.django_application(environ, start_response)


application = CloudRunHostCanonicalizingWSGIApplication(get_wsgi_application())
