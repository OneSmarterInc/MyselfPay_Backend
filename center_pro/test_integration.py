import time
from datetime import datetime,timedelta,time as clock
from decimal import Decimal
from zoneinfo import ZoneInfo
from unittest.mock import patch
from contextlib import nullcontext
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from .test_helpers import OriginClient
from .models import Center,Membership,Slot,WorkingHours,BookingRequest
from .integration import DomainConnection,sync_requests
import booking

class ExistingIdentityTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='assigned',email='assigned@example.test')
        self.center=Center.objects.create(name='Mapped center',directory_key='stable-center')
        self.membership=Membership.objects.create(center=self.center,user=self.user,role='staff')
        self.client=OriginClient(enforce_csrf_checks=True)
    def test_existing_admin_cookie_authenticates_session_and_protected_views(self):
        from .test_helpers import grant_admin_mfa
        admin=get_user_model().objects.create_superuser(username='cookie-admin',password='synthetic-test-only')
        self.client.force_login(admin)
        grant_admin_mfa(self.client,admin)
        session=self.client.get('/api/center-pro/session')
        self.assertTrue(session.json()['user']['is_admin'])
        response=self.client.get('/api/center-pro/admin/centers')
        self.assertEqual(response.status_code,200,response.json())

    def test_admin_email_can_be_assigned_but_center_login_cannot_use_admin_api(self):
        from .test_helpers import grant_admin_mfa
        from .models import MagicLink
        import hashlib,secrets
        admin=get_user_model().objects.create_superuser(username='dual-role',email='dual@example.test',password='synthetic-test-only')
        self.client.force_login(admin);grant_admin_mfa(self.client,admin)
        token=self.client.get('/api/center-pro/session?workspace=admin').json()['csrf_token']
        response=self.client.post(f'/api/center-pro/admin/centers/{self.center.pk}/assignments?workspace=admin',{'name':'Dual role','email':admin.email,'role':'owner'},format='json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code,201,response.data)
        raw=secrets.token_urlsafe(32)
        MagicLink.objects.create(user=admin,digest=hashlib.sha256(raw.encode()).hexdigest(),expires_at=timezone.now()+timedelta(minutes=10))
        result=self.client.post('/api/center-pro/auth/verify?workspace=center',{'token':raw},format='json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(result.status_code,200)
        self.assertFalse(result.json()['user']['is_admin'])
        self.assertEqual(result.json()['centers'][0]['role'],'owner')
        self.assertNotIn('admin_totp_verified',self.client.session)
        self.assertEqual(self.client.get('/api/center-pro/admin/centers?workspace=admin').status_code,401)
        self.center.directory_key=None;self.center.save(update_fields=['directory_key'])
        self.assertEqual(self.client.get(f'/api/center-pro/centers/{self.center.pk}/overview?workspace=center').status_code,200)

    def test_missing_csrf_returns_actionable_json_without_sending_mail(self):
        response=self.client.post('/api/center-pro/auth/request',{'email':'synthetic@example.test'},format='json',HTTP_X_CSRFTOKEN='')
        self.assertEqual(response.status_code,403)
        self.assertEqual(response.json()['code'],'csrf_failed')

    @patch('center_portal.centers', return_value=[])
    def test_magic_link_uses_existing_smtp_adapter(self, directory_lookup):
        from django.test import override_settings
        from .models import MagicLink
        with override_settings(CENTER_PRO_LOCAL_LINKS=False),patch('booking_mail.send_message') as send:
            csrf=self.client.get('/api/center-pro/session').json()['csrf_token']
            response=self.client.post('/api/center-pro/auth/request',{'email':self.user.email},format='json',HTTP_X_CSRFTOKEN=csrf)
            self.assertEqual(response.status_code,200)
            send.assert_called_once()
            self.assertEqual(send.call_args.args[1],self.user.email)
            self.assertEqual(MagicLink.objects.count(),1)

    def test_native_admin_workspace_keeps_admin_layout_and_assigns_access(self):
        from .test_helpers import grant_admin_mfa
        admin=get_user_model().objects.create_superuser(username='native-admin',password='synthetic-test-only')
        self.client.force_login(admin);grant_admin_mfa(self.client,admin)
        response=self.client.get('/admin/center-pro/')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'sp-admin-navigation')
        self.assertContains(response,'Manage access')
        self.assertNotIn('Location',response.headers)
        detail=self.client.get(f'/admin/center-pro/?center={self.center.pk}')
        token=detail.cookies.get('csrftoken').value if detail.cookies.get('csrftoken') else self.client.cookies['csrftoken'].value
        result=self.client.post(f'/admin/center-pro/?center={self.center.pk}',{'action':'assign','center':self.center.pk,'name':'Native owner','email':'native-owner@example.test','role':'owner','csrfmiddlewaretoken':token})
        self.assertEqual(result.status_code,302)
        self.assertTrue(Membership.objects.filter(center=self.center,user__email='native-owner@example.test',role='owner').exists())

    def test_patient_or_unverified_django_login_cannot_enter_center_api(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(f'/api/center-pro/centers/{self.center.pk}/overview').status_code,401)
    def test_verified_existing_center_cookie_uses_assignment_not_browser_role(self):
        from .integration_auth import center_cookie
        self.client.cookies[center_cookie()]='synthetic-cookie'
        with patch('center_pro.integration_auth.booking.connection',return_value=nullcontext(object())),patch('center_pro.integration_auth.center_portal.identity',return_value=(self.user.email,[{'id':'stable-center'}],'synthetic-csrf')):
            response=self.client.get('/api/center-pro/session')
            self.assertEqual(response.json()['centers'][0]['role'],'staff')
    def test_revocation_denies_next_protected_request(self):
        self.client.force_login(self.user)
        session=self.client.session;session['center_pro_verified_user']=self.user.pk;session.save()
        self.membership.delete()
        self.assertEqual(self.client.get(f'/api/center-pro/centers/{self.center.pk}/overview').status_code,404)
    def test_origin_rejected_for_pro_mutations(self):
        self.assertEqual(self.client.post('/api/center-pro/auth/request',{'email':self.user.email},format='json',HTTP_ORIGIN='https://untrusted.example').status_code,403)

class ExistingRequestTests(TestCase):
    def setUp(self):
        # Domain tables are unmanaged; create only the minimal fixture schema in the isolated test DB.
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE IF NOT EXISTS booking_requests (id TEXT PRIMARY KEY,owner_hash TEXT,payload TEXT,status TEXT,created REAL,closed REAL,link_expires REAL)')
            cursor.execute('CREATE TABLE IF NOT EXISTS center_request_routes (request_id TEXT PRIMARY KEY,clinic_id TEXT,email_hash TEXT)')
        self.cipher=patch('booking.cipher',return_value=Fernet(Fernet.generate_key()));self.cipher.start();self.addCleanup(self.cipher.stop)
        self.center=Center.objects.create(name='Mapped center',directory_key='stable-center')
        self.staff=get_user_model().objects.create_user(username='assigned-staff')
        Membership.objects.create(center=self.center,user=self.staff,role='staff')
        day=(timezone.now().astimezone(ZoneInfo('America/New_York'))+timedelta(days=4)).date()
        self.slot=Slot.objects.create(center=self.center,resource='Actual scanner',starts_at=datetime.combine(day,clock(9),ZoneInfo('America/New_York')),ends_at=datetime.combine(day,clock(9,30),ZoneInfo('America/New_York')))
        WorkingHours.objects.create(center=self.center,weekday=day.weekday(),opens=clock(8),closes=clock(17))
        self.payload={'clinic_id':'stable-center','first_name':'Synthetic Patient','service':'MRI','exam':'knee','price_text':'$400 fixed','slots':[{'date':day.isoformat(),'period':'morning'}],'timezone':'America/New_York','delivery_route':'center_email'}
        self.db=DomainConnection();self.db.execute('INSERT INTO booking_requests(id,owner_hash,payload,status,created,link_expires) VALUES(?,?,?,?,?,?)',('MSP-INTEGRATION','synthetic-owner',booking.pack(self.payload),'requested',time.time(),time.time()+86400*10))
        self.db.execute('INSERT INTO center_request_routes(request_id,clinic_id) VALUES(?,?)',('MSP-INTEGRATION','stable-center'))
        self.client=OriginClient();self.client.force_authenticate(self.staff)
    def test_import_is_idempotent_and_preserves_snapshot(self):
        sync_requests(self.center);sync_requests(self.center)
        item=BookingRequest.objects.get(legacy_id='MSP-INTEGRATION')
        self.assertEqual(BookingRequest.objects.count(),1);self.assertEqual(item.price_snapshot,Decimal('400'))
    def test_confirmation_updates_existing_patient_status(self):
        sync_requests(self.center);item=BookingRequest.objects.get(legacy_id='MSP-INTEGRATION')
        result=self.client.post(f'/api/center-pro/centers/{self.center.pk}/requests/{item.pk}/confirm',{'slot_id':self.slot.pk},format='json')
        self.assertEqual(result.status_code,200,result.data)
        row=self.db.execute('SELECT * FROM booking_requests WHERE id=?',('MSP-INTEGRATION',)).fetchone()
        self.assertEqual(row['status'],'scheduled');self.assertEqual(booking.unpack(row)['approved_slot']['time'],'09:00')
    def test_closed_legacy_request_cannot_be_confirmed(self):
        sync_requests(self.center);item=BookingRequest.objects.get(legacy_id='MSP-INTEGRATION')
        self.db.execute("UPDATE booking_requests SET status='cancelled' WHERE id=?",('MSP-INTEGRATION',))
        result=self.client.post(f'/api/center-pro/centers/{self.center.pk}/requests/{item.pk}/confirm',{'slot_id':self.slot.pk},format='json')
        self.assertEqual(result.status_code,409)

class SMTPConfigurationTests(TestCase):
    def test_ssl_port_587_is_rejected_by_admin_form(self):
        from api.admin import SMTPConfigurationForm
        form=SMTPConfigurationForm(data={'host':'smtp.ionos.com','port':587,'security':'ssl','from_name':'MySelfPay'})
        self.assertFalse(form.is_valid());self.assertIn('security',form.errors)
    def test_starttls_port_465_is_rejected_by_admin_form(self):
        from api.admin import SMTPConfigurationForm
        form=SMTPConfigurationForm(data={'host':'smtp.ionos.com','port':465,'security':'starttls','from_name':'MySelfPay'})
        self.assertFalse(form.is_valid());self.assertIn('security',form.errors)


class DirectoryPricingTests(TestCase):
    def test_directory_prices_compare_only_matching_published_offers(self):
        import json
        from types import SimpleNamespace
        from rest_framework.test import APIRequestFactory
        from .owner_views import DirectoryPricesView
        from .integration import Result
        offers = [
            {'entry_key':key,'amount_cents':price,'payload':json.dumps({'mod':'MRI','scope':'Brain','contrast':contrast,'type':'fixed'})}
            for key,price,contrast in [('own',40000,'without'),('higher',50000,'without'),('different',60000,'with'),('quote',None,'without')]
        ]
        entries=[{'entry_key':key,'payload':json.dumps({'name':key,'mri':'Quote required','ct':'Not listed'})} for key in ['own','higher','different','quote']]
        view=DirectoryPricesView()
        request=view.initialize_request(APIRequestFactory().get('/prices',HTTP_HOST='localhost'))
        with patch.object(view,'owner',return_value=SimpleNamespace(center=SimpleNamespace(directory_key='own'))), patch('core.active_batch',return_value='batch'), patch('center_pro.integration.DomainConnection') as db:
            db.return_value.execute.side_effect=[Result(offers),Result(entries)]
            response=view.get(request,1)
        self.assertEqual(response.data['current_center']['id'],'own')
        rows={r['id']:r for r in response.data['results']}
        self.assertEqual(rows['higher']['comparisons'][0]['difference'],100)
        self.assertEqual(rows['different']['comparisons'],[])
        self.assertNotIn('quote',rows)
        self.assertTrue(response.data['results'][0]['current'])


class OwnerLegacyFeatureTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='email-owner',email='owner@example.test')
        self.center=Center.objects.create(name='Assigned owner listing',directory_key='assigned-listing')
        self.member=Membership.objects.create(user=self.user,center=self.center,role='owner')
        self.client=OriginClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        session=self.client.session;session['center_pro_verified_user']=self.user.pk;session.save()
    def test_verified_owner_gets_scoped_legacy_token_and_revocation_applies(self):
        from django.core import signing
        from .integration_auth import owner_portal_identity
        from .integration import Result
        seen=[]
        def dashboard(path,token):
            seen.append(token)
            return {'center_csrf':'test','centers':[],'requests':[]}
        with patch('center_portal.dashboard',side_effect=dashboard):
            response=self.client.get('/api/center/dashboard',{'owner_center':self.center.pk})
        self.assertEqual(response.status_code,200,response.data)
        value=signing.loads(seen[0].split(':',1)[1],salt='center-owner-portal')
        self.assertEqual(value['center'],self.center.pk)
        self.assertEqual(value['user'],self.user.pk)
        self.member.delete()
        with self.assertRaises(booking.BookingError):
            owner_portal_identity(None,seen[0])
    def test_staff_and_unassigned_owner_cannot_use_owner_forms(self):
        self.member.role='staff';self.member.save()
        self.assertEqual(self.client.get('/api/center/dashboard',{'owner_center':self.center.pk}).status_code,404)
    def test_owner_write_requires_django_csrf(self):
        response=self.client.post('/api/center/price?owner_center='+str(self.center.pk),{},format='json')
        self.assertEqual(response.status_code,403)


class AlternativeTimeTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='alternative-owner')
        self.center=Center.objects.create(name='Alternative center',timezone='America/New_York')
        Membership.objects.create(center=self.center,user=self.user,role='owner')
        self.booking=BookingRequest.objects.create(center=self.center,reference='ALT-1',patient_name='Test',exam='MRI',source='myselfpay',preferred_windows=[])
        self.start=timezone.now()+timedelta(days=5)
    def confirm(self,booking=None):
        from .services import confirm_alternative
        return confirm_alternative(user=self.user,center_id=self.center.pk,booking_id=(booking or self.booking).pk,starts_at=self.start,ends_at=self.start+timedelta(minutes=30),resource='Scanner A',reason='Agreed with patient')
    def test_owner_can_confirm_alternative_without_saved_hours(self):
        self.assertEqual(self.confirm().status,'confirmed')
    def test_conflicting_appointment_is_rejected(self):
        from .services import Conflict
        self.confirm()
        other=BookingRequest.objects.create(center=self.center,reference='ALT-2',patient_name='Test',exam='MRI',source='myselfpay',preferred_windows=[])
        with self.assertRaises(Conflict):self.confirm(other)
    def test_unassigned_user_cannot_confirm(self):
        from rest_framework.exceptions import PermissionDenied
        Membership.objects.all().delete()
        with self.assertRaises(PermissionDenied):self.confirm()


class DefaultDirectoryOwnerTests(TestCase):
    def test_directory_email_gets_owner_and_staff_assignment_is_preserved(self):
        from .access import directory_owner_access
        from .integration import Result
        import json
        listing={'id':'directory-owner','name':'Directory center'}
        with patch('center_portal.centers',return_value=[listing]),patch('core.active_batch',return_value='batch'),patch('center_pro.integration.DomainConnection') as db:
            db.return_value.execute.return_value=Result([{'payload':json.dumps({'name':'Directory center','states':'OH'})}])
            user=directory_owner_access('Owner@Example.test')
            self.assertFalse(user.has_usable_password())
            member=Membership.objects.get(user=user)
            self.assertEqual(member.role,'owner')
            member.role='staff';member.save()
            directory_owner_access('owner@example.test')
            member.refresh_from_db();self.assertEqual(member.role,'staff')
            self.assertEqual(Membership.objects.count(),1)
    def test_unknown_mailbox_creates_no_account(self):
        from .access import directory_owner_access
        with patch('center_portal.centers',return_value=[]):
            self.assertIsNone(directory_owner_access('unknown@example.test'))
        self.assertEqual(get_user_model().objects.count(),0)
    def test_revoked_center_stays_manual_only(self):
        from .access import directory_owner_access
        user=get_user_model().objects.create_user(username='prior',email='owner@example.test')
        center=Center.objects.create(name='Revoked',directory_key='revoked')
        from .models import AuditEvent
        AuditEvent.objects.create(center=center,actor=user,action='access.revoked',reference='MEMBER-1')
        with patch('center_portal.centers',return_value=[{'id':'revoked','name':'Revoked'}]):
            directory_owner_access(user.email)
        self.assertFalse(Membership.objects.filter(user=user).exists())


class CenterCHROfferTests(TestCase):
    def test_chr_uses_published_center_location_and_offer(self):
        import json
        from types import SimpleNamespace
        from rest_framework.test import APIRequestFactory
        from .owner_views import CenterCHROfferView
        from .integration import Result
        view=CenterCHROfferView()
        request=view.initialize_request(APIRequestFactory().get('/chr-offer',HTTP_HOST='localhost'))
        member=SimpleNamespace(center=SimpleNamespace(directory_key='center-key'))
        entry={'name':'Center','states':'OH','locations':[{'zip':'45432','state':'OH'}]}
        offer={'id':'published-mri','mod':'MRI','contrast':'without','type':'fixed'}
        with patch.object(view,'owner',return_value=member),patch('core.active_batch',return_value='active'),patch('center_pro.integration.DomainConnection') as db:
            db.return_value.execute.side_effect=[Result([{'payload':json.dumps(entry)}]),Result([{'payload':json.dumps(offer),'amount_cents':40000}])]
            response=view.get(request,1)
        self.assertEqual(response.data['entry']['locations'][0]['zip'],'45432')
        self.assertEqual(response.data['offer']['amount'],400)
        self.assertEqual(response.data['entry']['booking_id'],'center-key')


class CenterCHRAuthenticationTests(TestCase):
    def test_owner_session_can_compare_without_patient_credentials(self):
        from rest_framework.test import APIRequestFactory,force_authenticate
        from .owner_views import CenterCHRComparisonView
        user=get_user_model().objects.create_user(username='chr-center-owner')
        center=Center.objects.create(name='Center')
        member=Membership.objects.create(user=user,center=center,role='owner')
        request=APIRequestFactory().get('/chr-index',{'zip':'45432','mri':'400','ct':'300'},HTTP_HOST='localhost');force_authenticate(request,user=user)
        with patch('center_pro.integration.sync_requests'),patch('chr_index.compare',return_value={'zip':'45432','hospitals':[]}) as compare:
            response=CenterCHRComparisonView.as_view()(request,center_id=center.pk)
        self.assertEqual(response.status_code,200);compare.assert_called_once()
        member.delete()
        request=APIRequestFactory().get('/chr-index',HTTP_HOST='localhost');force_authenticate(request,user=user)
        response=CenterCHRComparisonView.as_view()(request,center_id=center.pk)
        self.assertEqual(response.status_code,404)
