"""Rate limits count each visitor separately, using the same trusted IP as the audit log."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ.setdefault('DJANGO_DEBUG', 'true')
import django
django.setup()
from django.test import RequestFactory, override_settings
import core
from api import views
from api.throttles import DatabaseWindowThrottle


class TwoPerMinute(DatabaseWindowThrottle):
    scope = 'visitor-test'
    limit = 2


class VisitorIPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / 'limits.db')
        core.initialize(self.db)
        self.settings = override_settings(DOMAIN_DATABASE=self.db)
        self.settings.enable()

    def tearDown(self):
        self.settings.disable()
        self.temp.cleanup()

    def request(self, visitor):
        # nginx on the server: REMOTE_ADDR is always 127.0.0.1; the visitor arrives in X-Forwarded-For.
        return RequestFactory().get('/api/x', REMOTE_ADDR='127.0.0.1', HTTP_X_FORWARDED_FOR=visitor)

    def hits(self, visitor, count):
        return [TwoPerMinute().allow_request(self.request(visitor), None) for _ in range(count)]

    def test_each_visitor_has_a_separate_bucket_behind_the_proxy(self):
        with patch.dict(os.environ, {'AUDIT_TRUSTED_PROXIES': '1'}):
            self.assertEqual(self.hits('198.51.100.1', 4), [True, True, False, False])
            self.assertEqual(self.hits('198.51.100.2', 3), [True, True, False])
            self.assertEqual(views.client_ip(self.request('198.51.100.9')), '198.51.100.9')

    def test_without_a_proxy_setting_the_connecting_address_is_used(self):
        with patch.dict(os.environ, {'AUDIT_TRUSTED_PROXIES': '0'}):
            self.assertEqual(views.client_ip(self.request('198.51.100.1')), '127.0.0.1')

    def test_throttle_and_audit_log_agree_on_the_address(self):
        import audit
        with patch.dict(os.environ, {'AUDIT_TRUSTED_PROXIES': '1'}):
            request = self.request('203.0.113.7')
            self.assertEqual(views.client_ip(request), audit.client_ip(request))
            self.assertEqual(views.client_ip(request), '203.0.113.7')
