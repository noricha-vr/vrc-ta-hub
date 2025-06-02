from drf_spectacular.extensions import OpenApiAuthenticationExtension


class APIKeyAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = 'api_v1.authentication.APIKeyAuthentication'
    name = 'APIKeyAuth'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'apiKey',
            'in': 'header',
            'name': 'Authorization',
            'description': 'APIキーを使用した認証。形式: Bearer YOUR_API_KEY',
        }