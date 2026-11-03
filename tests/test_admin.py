from django.test import TransactionTestCase, Client, override_settings
from django.db import connection
from django.contrib.auth import get_user_model
from api.models import PortalAccount, PortalMember, PortalSetting, TwilioConfiguration
from admin_settings import twilio_configuration
import accounts
import secrets
import time


@override_settings(ALLOWED_HOSTS=['testserver'], FRONTEND_ORIGIN='http://testserver',
                   CSRF_TRUSTED_ORIGINS=['http://testserver'], SECURE_SSL_REDIRECT=False)
class AdminTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as editor:
            for model in (PortalMember, PortalAccount, PortalSetting):
                editor.create_model(model)
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE sessions (token_hash TEXT PRIMARY KEY,member_id TEXT,csrf TEXT,expires REAL)')

    @classmethod
    def tearDownClass(cls):
        with connection.cursor() as cursor:
            cursor.execute('DROP TABLE sessions')
        with connection.schema_editor() as editor:
            for model in (PortalSetting, PortalAccount, PortalMember): editor.delete_model(model)
        super().tearDownClass()

    def setUp(self):
        PortalAccount.objects.all().delete()
        PortalMember.objects.all().delete()
        PortalSetting.objects.all().delete()
        self.admin = get_user_model().objects.create_superuser('admin-test', 'admin@example.test', 'Synthetic-admin-passphrase!')
        self.client.force_login(self.admin)
        salt = secrets.token_hex(16)
        PortalMember.objects.create(id='portal-test',tenant='selfpay',name='Portal User',email='portal@example.test',salt=salt,password_hash='scrypt-v1$'+accounts.password_hash('Synthetic-portal-password!',salt))
        PortalAccount.objects.create(member_id='portal-test',email='portal@example.test',phone='+15550102222',created=time.time())

    def test_login_dashboard_and_access_separation(self):
        anonymous=Client()
        self.assertEqual(anonymous.get('/admin/').status_code,302)
        login=anonymous.get('/admin/login/')
        self.assertContains(login,'Administrator sign in')
        self.assertIn('style-src',login['Content-Security-Policy'])
        staff=get_user_model().objects.create_user('staff',password='Synthetic-staff-password!',is_staff=True)
        anonymous.force_login(staff)
        self.assertEqual(anonymous.get('/admin/').status_code,302)
        response=self.client.get('/admin/')
        self.assertContains(response,'Registered users')
        self.assertContains(response,'Twilio configuration')
        self.assertEqual(self.client.get('/admin/api/portalaccount/').status_code,200)
        self.assertNotEqual(__import__('django.conf',fromlist=['settings']).settings.SESSION_COOKIE_NAME,
                            __import__('django.conf',fromlist=['settings']).settings.SELFPAY_SESSION_COOKIE_NAME)

    def test_registered_user_edit_reset_suspend_and_restore(self):
        response=self.client.post('/admin/api/portalaccount/portal-test/change/',
            {'name':'Updated User','email':'updated@example.com','phone':'+15550103333','new_password':'New-synthetic-password!','_save':'Save'},HTTP_ORIGIN='http://testserver')
        self.assertEqual(response.status_code,302)
        member=PortalMember.objects.get(pk='portal-test')
        self.assertEqual(member.name,'Updated User')
        self.assertEqual(member.email,'updated@example.com')
        self.assertEqual(PortalSetting.objects.get(pk='account-disabled:portal-test').value,'1')
        self.assertEqual(member.password_hash,'scrypt-v1$'+accounts.password_hash('New-synthetic-password!',member.salt))
        response=self.client.post('/admin/api/portalaccount/portal-test/change/',
            {'name':'Updated User','email':'updated@example.com','phone':'+15550103333','active':'on','_save':'Save'},HTTP_ORIGIN='http://testserver')
        self.assertEqual(response.status_code,302)
        self.assertEqual(PortalSetting.objects.get(pk='account-disabled:portal-test').value,'0')

    def test_twilio_secret_is_encrypted_and_never_reflected(self):
        fields={'api_key_sid':'SK'+'1'*32,'api_key_secret':'synthetic-secret-value','account_sid':'AC'+'2'*32,'verify_service_sid':'VA'+'3'*32,'_save':'Save'}
        response=self.client.post('/admin/api/twilioconfiguration/add/',fields,HTTP_ORIGIN='http://testserver')
        self.assertEqual(response.status_code,302)
        row=TwilioConfiguration.objects.get(pk=1)
        self.assertNotIn('synthetic-secret-value',row.secret_encrypted)
        self.assertEqual(twilio_configuration()['api_key_secret'],'synthetic-secret-value')
        response=self.client.get('/admin/api/twilioconfiguration/1/change/')
        self.assertNotContains(response,'synthetic-secret-value')
        self.assertNotContains(response,row.secret_encrypted)
        fields['api_key_secret']=''
        self.client.post('/admin/api/twilioconfiguration/1/change/',fields,HTTP_ORIGIN='http://testserver')
        self.assertEqual(twilio_configuration()['api_key_secret'],'synthetic-secret-value')
        fields['verify_service_sid']='invalid'
        response=self.client.post('/admin/api/twilioconfiguration/1/change/',fields,HTTP_ORIGIN='http://testserver')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Use a valid VA SID')

    def test_admin_requires_csrf(self):
        csrf_client=Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        response=csrf_client.post('/admin/api/twilioconfiguration/add/',{'api_key_sid':'SK'+'1'*32},HTTP_ORIGIN='http://testserver')
        self.assertEqual(response.status_code,403)

    def test_approved_requests_and_complete_details_are_superuser_only(self):
        import test_booking
        import booking
        fixture=test_booking.BookingTests()
        fixture.setUp()
        try:
            item=fixture.create()
            session=fixture.authorize_clinic(item)
            booking.clinic_details(fixture.db,item['id'],session,'scheduled')
            with override_settings(DOMAIN_DATABASE=str(fixture.db)):
                dashboard=self.client.get('/admin/')
                self.assertContains(dashboard,'Approved requests')
                listing=self.client.get('/admin/requests/?filter=approved')
                self.assertContains(listing,item['id'])
                self.assertContains(listing,'Approved')
                details=self.client.get('/admin/requests/'+item['id']+'/')
                self.assertContains(details,'patient@example.test')
                self.assertContains(details,'appointments@example.test')
                self.assertContains(details,'Synthetic Jamie')
                self.assertContains(details,'Morning')
                self.assertNotContains(details,item['tracking_token'])
                anonymous=Client()
                self.assertEqual(anonymous.get('/admin/requests/').status_code,302)
                staff=get_user_model().objects.create_user('booking-staff',password='Synthetic-staff-password!',is_staff=True)
                anonymous.force_login(staff)
                self.assertEqual(anonymous.get('/admin/requests/'+item['id']+'/').status_code,302)
        finally:
            fixture.tearDown()
