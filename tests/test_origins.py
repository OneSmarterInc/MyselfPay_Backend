import os
import unittest
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ.setdefault('DJANGO_DEBUG', 'true')
import django
django.setup()
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from api.middleware import RequestSecurityMiddleware


@override_settings(ALLOWED_HOSTS=['testserver'], FRONTEND_ORIGIN='https://app.example.test',
                   ADDITIONAL_FRONTEND_ORIGINS=['https://admin.example.test'])
class OriginTests(SimpleTestCase):
    def post(self, origin):
        middleware = RequestSecurityMiddleware(lambda request: HttpResponse('ok'))
        extra = {'HTTP_ORIGIN': origin} if origin else {}
        return middleware(RequestFactory().post('/api/anything', **extra)).status_code

    def test_only_listed_origins_may_change_data(self):
        self.assertEqual(self.post('https://app.example.test'), 200)
        self.assertEqual(self.post('https://admin.example.test'), 200)
        self.assertEqual(self.post('https://evil.example.test'), 403)
        self.assertEqual(self.post('https://admin.example.test.evil.test'), 403)
        self.assertEqual(self.post(None), 403)

    @override_settings(ADDITIONAL_FRONTEND_ORIGINS=[])
    def test_extra_origins_are_off_by_default(self):
        self.assertEqual(self.post('https://admin.example.test'), 403)
