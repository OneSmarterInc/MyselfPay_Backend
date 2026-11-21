"""Real admin sessions and ORM; no patient authentication or outbound messages."""
import time
from unittest.mock import patch
import pyotp
from django.contrib.admin import AdminSite
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, Client, RequestFactory
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from api.models import AdminTOTP
from api.admin import admin_site, SelfPayAdminSite
from admin_settings import secret_cipher
import admin_totp


@override_settings(ALLOWED_HOSTS=['testserver'], FRONTEND_ORIGIN='http://testserver',
                   CSRF_TRUSTED_ORIGINS=['http://testserver'], SECURE_SSL_REDIRECT=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AdminTOTPTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('admin', password='Synthetic-password!')
        self.client.defaults['HTTP_ORIGIN']='http://testserver'
        self.seed=pyotp.random_base32()
        self.device=AdminTOTP.objects.create(user=self.user,secret_encrypted=secret_cipher('admin-totp').encrypt(self.seed.encode()).decode())
        # Isolate authentication from unrelated domain dashboard/audit queries.
        self.patches=[patch('api.throttles.AdminLoginThrottle.allow_request', return_value=True), patch('admin_audit.wrap', side_effect=lambda view:view),
                      patch.object(SelfPayAdminSite,'each_context',AdminSite.each_context)]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def password(self, client=None):
        client=client or self.client
        return client.post(reverse('admin:login'),{'username':'admin','password':'Synthetic-password!','next':'/admin/password_change/'})

    def code(self):return pyotp.TOTP(self.seed).now()

    def test_password_is_required_before_enrollment(self):
        self.assertEqual(self.client.get(reverse('admin:totp')).status_code,302)
        self.assertEqual(self.client.post(reverse('admin:totp'),{'code':self.code()}).status_code,302)

    def test_password_session_alone_cannot_open_admin_pages(self):
        self.assertRedirects(self.password(), reverse('admin:totp'), fetch_redirect_response=False)
        for path in ['/admin/','/admin/auth/user/','/admin/mailer/','/admin/requests/']:
            self.assertEqual(self.client.get(path).status_code,302)
        response=self.client.get(reverse('admin:totp'))
        self.assertContains(response,'Set up your authenticator')
        self.assertContains(response,'<svg',html=False)
        self.assertIn('no-store',response['Cache-Control'])

    def test_enrollment_saves_encrypted_secret_and_recovery_codes_once(self):
        self.password()
        response=self.client.post(reverse('admin:totp'),{'code':self.code()})
        self.assertContains(response,'Save your recovery codes')
        codes=response.context_data['recovery_codes']
        self.assertEqual(len(codes),8)
        self.device.refresh_from_db()
        self.assertTrue(self.device.enabled)
        self.assertNotIn(self.seed,self.device.secret_encrypted)
        self.assertNotIn(codes[0],str(self.device.recovery_hashes))
        self.assertEqual(self.client.get('/admin/password_change/').status_code,200)
        self.assertEqual(self.client.get(reverse('admin:totp')).status_code,302)
        self.assertFalse(admin_totp.verify_code(self.user,self.code())[0])

    def test_recovery_code_is_one_time_and_not_offered_during_setup(self):
        ok,_,result=admin_totp.verify_code(self.user,self.code())
        self.assertTrue(ok)
        recovery=result[1][0]
        self.assertTrue(admin_totp.verify_code(self.user,recovery)[0])
        self.assertFalse(admin_totp.verify_code(self.user,recovery)[0])

    def test_five_failures_lock_then_expire_without_reusing_counter(self):
        for _ in range(5):self.assertFalse(admin_totp.verify_code(self.user,'invalid')[0])
        self.assertFalse(admin_totp.verify_code(self.user,self.code())[0])
        self.device.refresh_from_db();self.assertIsNotNone(self.device.locked_until)
        self.device.locked_until=timezone.now()-timedelta(seconds=1);self.device.save()
        self.assertTrue(admin_totp.verify_code(self.user,self.code())[0])

    def test_csrf_is_required_even_with_correct_password_session(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        session=client.session;session['admin_totp_password_at']=time.time();session.save()
        self.assertEqual(client.post(reverse('admin:totp'),{'code':self.code()}).status_code,403)

    def test_enrollment_requires_recent_password_and_nonadmin_is_rejected(self):
        self.password();session=self.client.session;session['admin_totp_password_at']=time.time()-601;session.save()
        self.assertRedirects(self.client.get(reverse('admin:totp')),reverse('admin:login'),fetch_redirect_response=False)
        normal=get_user_model().objects.create_user('normal',password='Synthetic-password!')
        self.client.force_login(normal)
        self.assertEqual(self.client.get(reverse('admin:totp')).status_code,302)

    def test_new_login_requires_new_code_and_reset_revokes_proof(self):
        self.password();self.client.post(reverse('admin:totp'),{'code':self.code()})
        self.client.logout();self.assertEqual(self.password().status_code,302)
        self.assertEqual(self.client.get('/admin/password_change/').status_code,302)
        self.assertFalse(admin_totp.verify_code(self.user,self.code())[0])
        AdminTOTP.objects.filter(pk=self.device.pk).delete()
        request=RequestFactory().get('/admin/');request.user=self.user;request.session=self.client.session
        self.assertFalse(admin_site.has_permission(request))

    def test_open_redirect_is_rejected(self):
        request=RequestFactory().get('/admin/totp/');request.session={}
        self.assertEqual(admin_totp.safe_next(request,'https://evil.example/'),reverse('admin:index'))
        self.assertEqual(admin_totp.safe_next(request,'/admin/totp/'),reverse('admin:index'))

    def test_missing_or_damaged_secret_fails_closed(self):
        self.password();self.device.secret_encrypted='invalid';self.device.save()
        response=self.client.post(reverse('admin:totp'),{'code':'123456'})
        self.assertContains(response,'Enrollment is unavailable')
        self.assertNotIn('admin_totp_verified',self.client.session)
