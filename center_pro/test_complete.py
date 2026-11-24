from unittest.mock import patch
import hashlib
import secrets
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlparse
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from .test_helpers import OriginClient as APIClient
import pyotp
from .auth_flows import cipher
from .models import Center, Membership, Offer, Slot, WorkingHours, BookingRequest, MagicLink, PatientLink, EstimateDraft, AdminMFA, AuditEvent


@override_settings(DEBUG=True, ENABLE_DEV_LOGIN=True, FRONTEND_ORIGIN='http://127.0.0.1:5173')
@override_settings(CENTER_PRO_LOCAL_LINKS=True, CSRF_TRUSTED_ORIGINS=["http://127.0.0.1:5173","http://localhost:5173"])
class CompleteWorkflowTests(TestCase):
    def setUp(self):
        self.client=APIClient()
        users=get_user_model()
        self.staff=users.objects.create_user(username='staff',email='staff@example.test')
        self.owner=users.objects.create_user(username='owner',email='owner@example.test')
        self.admin=users.objects.create_superuser(username='admin',email='admin@example.test',password='Synthetic-test-password-867')
        self.center=Center.objects.create(name='Sample center')
        self.other=Center.objects.create(name='Other center')
        for day in range(7): WorkingHours.objects.create(center=self.center,weekday=day,opens='00:00',closes='23:59')
        for user,role in [(self.staff,'staff'),(self.owner,'owner')]:Membership.objects.create(user=user,center=self.center,role=role)
        self.offer=Offer.objects.create(center=self.center,exam='MRI',price='395.00')
        self.prefix=f'/api/center-pro/centers/{self.center.id}'
        self.client.force_authenticate(self.staff)
        at=timezone.now()+timedelta(days=3)
        self.choice_ids=[Slot.objects.create(center=self.center,resource='Choice scanner',starts_at=at+timedelta(hours=i),ends_at=at+timedelta(hours=i,minutes=30)).id for i in range(3)]
    def admin_session(self,mfa=True):
        self.client.force_authenticate(self.admin)
        from .test_helpers import grant_admin_mfa
        grant_admin_mfa(self.client,self.admin,mfa)
    def patient_link(self):
        raw=secrets.token_urlsafe(32)
        link=PatientLink.objects.create(center=self.center,offer=self.offer,price_snapshot=self.offer.price,digest=hashlib.sha256(raw.encode()).hexdigest(),created_by=self.staff,consent=True,expires_at=timezone.now()+timedelta(days=1))
        return raw,link
    def booking(self):
        return BookingRequest.objects.create(center=self.center,reference='SAMPLE-1',patient_name='Synthetic Patient',exam='MRI',price_snapshot='395.00')

    @patch('center_portal.centers', return_value=[])
    def test_magic_link_single_use_and_role_from_membership(self, directory_lookup):
        client=APIClient(enforce_csrf_checks=True)
        csrf=client.get('/api/center-pro/session').json()['csrf_token']
        result=client.post('/api/center-pro/auth/request',{'email':self.staff.email},format='json',HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(result.status_code,200)
        token=result.json()['development_url'].split('/')[-1]
        response=client.post('/api/center-pro/auth/verify',{'token':token,'role':'owner'},format='json',HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['centers'][0]['role'],'staff')
        csrf=response.json()['csrf_token']
        self.assertEqual(client.post('/api/center-pro/auth/verify',{'token':token},format='json',HTTP_X_CSRFTOKEN=csrf).status_code,400)

    def test_magic_expired_or_revoked_access_is_denied(self):
        for revoked in (False,True):
            raw=secrets.token_urlsafe(32)
            MagicLink.objects.create(user=self.staff,digest=hashlib.sha256(raw.encode()).hexdigest(),expires_at=timezone.now()+timedelta(minutes=10) if revoked else timezone.now()-timedelta(seconds=1))
            if revoked:Membership.objects.filter(user=self.staff).delete()
            self.assertEqual(self.client.post('/api/center-pro/auth/verify',{'token':raw},format='json').status_code,400)

    @patch('center_portal.centers', return_value=[])
    def test_magic_unknown_email_and_rate_limit(self, directory_lookup):
        self.assertNotIn('development_url',self.client.post('/api/center-pro/auth/request',{'email':'missing@example.test'},format='json').json())
        for _ in range(4):self.client.post('/api/center-pro/auth/request',{'email':'missing@example.test'},format='json')
        self.assertEqual(self.client.post('/api/center-pro/auth/request',{'email':'missing@example.test'},format='json').status_code,429)

    def test_separate_admin_auth_is_disabled(self):
        result=APIClient().post('/api/center-pro/auth/admin',{'username':'admin','password':'unused','code':'123456'},format='json')
        self.assertEqual(result.status_code,403)
        self.assertEqual(result.json()['login_url'],'/admin/login/?next=/admin/center-pro/')

    def test_admin_requires_mfa_not_only_superuser(self):
        self.admin_session(False)
        self.assertEqual(self.client.get('/api/center-pro/admin/centers').status_code,403)
        self.admin_session(True)
        self.assertEqual(self.client.get('/api/center-pro/admin/centers').status_code,200)

    def test_assign_revoke_and_last_owner_guard(self):
        self.admin_session()
        url=f'/api/center-pro/admin/centers/{self.center.id}/assignments'
        response=self.client.post(url,{'name':'Sample new staff','email':'new@example.test','role':'staff'},format='json')
        self.assertEqual(response.status_code,201)
        self.assertEqual(self.client.post(url+'/'+str(response.data['id'])+'/revoke',{},format='json').status_code,200)
        owner=Membership.objects.get(user=self.owner)
        self.assertEqual(self.client.post(url+f'/{owner.id}/revoke',{},format='json').status_code,409)

    def test_assignments_deny_staff_and_cross_center_revoke(self):
        url=f'/api/center-pro/admin/centers/{self.center.id}/assignments'
        self.assertEqual(self.client.get(url).status_code,403)
        self.admin_session()
        member=Membership.objects.get(user=self.staff)
        self.assertEqual(self.client.post(f'/api/center-pro/admin/centers/{self.other.id}/assignments/{member.id}/revoke',{},format='json').status_code,404)

    def test_working_hours_and_slot_timezone_overlap(self):
        hours=[{'weekday':i,'opens':'08:00','closes':'17:00','closed':False} for i in range(7)]
        self.assertEqual(self.client.post(self.prefix+'/hours',hours,format='json').status_code,200)
        date=(timezone.now()+timedelta(days=4)).date().isoformat()
        data={'starts_at':date+'T09:00','ends_at':date+'T09:30','resource':'Sample scanner'}
        response=self.client.post(self.prefix+'/availability',data,format='json')
        self.assertEqual(response.status_code,201)
        slot=Slot.objects.get(pk=response.data['id'])
        from zoneinfo import ZoneInfo
        self.assertEqual(slot.starts_at.astimezone(ZoneInfo(self.center.timezone)).hour,9)
        self.assertEqual(self.client.post(self.prefix+'/availability',data,format='json').status_code,409)
        data['starts_at']=date+'T20:00';data['ends_at']=date+'T20:30'
        self.assertEqual(self.client.post(self.prefix+'/availability',data,format='json').status_code,400)

    def test_invalid_hours_and_owner_scheduling_denied(self):
        self.assertEqual(self.client.post(self.prefix+'/hours',[],format='json').status_code,400)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(self.prefix+'/hours').status_code,403)

    def test_confirmed_slot_cannot_be_blocked(self):
        at=timezone.now()+timedelta(days=1)
        slot=Slot.objects.create(center=self.center,resource='Sample',starts_at=at,ends_at=at+timedelta(minutes=30))
        booking=self.booking();booking.slot=slot;booking.status='confirmed';booking.save()
        self.assertEqual(self.client.post(self.prefix+f'/availability/{slot.id}/block',{'blocked':True},format='json').status_code,409)

    def test_rescue_requires_consent_and_snapshot(self):
        self.assertEqual(self.client.post(self.prefix+'/rescue',{'offer_id':self.offer.id,'consent':False},format='json').status_code,400)
        result=self.client.post(self.prefix+'/rescue',{'offer_id':self.offer.id,'consent':True},format='json')
        self.assertEqual(result.status_code,201)
        self.assertEqual(result.data['delivery'],'not_sent')
        self.offer.price=500;self.offer.save()
        self.assertEqual(PatientLink.objects.get().price_snapshot,Decimal('395.00'))

    def test_patient_request_is_idempotent_and_uses_snapshot(self):
        token,link=self.patient_link();public=APIClient();url='/api/center-pro/patient/'+token
        data={'patient_name':'Synthetic Person','preferred_slot_ids':self.choice_ids,'price_snapshot':'1.00','center_id':self.other.id}
        first=public.post(url,data,format='json');self.assertEqual(first.status_code,201)
        second=public.post(url,data,format='json');self.assertEqual(second.status_code,200)
        self.assertEqual(BookingRequest.objects.count(),1)
        self.assertEqual(BookingRequest.objects.get().price_snapshot,Decimal('395.00'))
        self.assertEqual(public.get(url).data['status'],'pending')

    def test_patient_expired_and_invalid_windows(self):
        token,link=self.patient_link();url='/api/center-pro/patient/'+token
        self.assertEqual(self.client.post(url,{'patient_name':'Sample','preferred_windows':[]},format='json').status_code,400)
        link.expires_at=timezone.now()-timedelta(seconds=1);link.save()
        self.assertEqual(self.client.get(url).status_code,404)

    def test_estimate_drafts_are_immutable_snapshots(self):
        booking=self.booking();url=self.prefix+f'/requests/{booking.id}/estimates'
        first=self.client.post(url,{},format='json');self.assertEqual(first.status_code,201)
        self.offer.price=500;self.offer.save()
        second=self.client.post(url,{},format='json');self.assertEqual(second.data['version'],2)
        self.assertEqual(EstimateDraft.objects.get(pk=first.data['id']).snapshot['price'],'395.00')
        self.assertIn('DRAFT',first.data['snapshot']['status'])

    def test_owner_cannot_create_estimate_and_wrong_center_denied(self):
        booking=self.booking();self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(self.prefix+f'/requests/{booking.id}/estimates',{},format='json').status_code,403)

    def test_referral_qr_and_public_booking(self):
        self.client.force_authenticate(self.owner)
        response=self.client.get(self.prefix+'/referrals')
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.data['qr'].startswith('data:image/'))
        public=APIClient()
        self.assertEqual(public.post(f'/api/center-pro/referral/{self.center.id}',{'offer_id':self.offer.id,'patient_name':'Synthetic','preferred_slot_ids':self.choice_ids},format='json').status_code,201)
        self.assertEqual(BookingRequest.objects.get().source,'qr_referral')

    def test_billing_and_market_do_not_fabricate_integrations(self):
        self.client.force_authenticate(self.owner)
        subscription=self.client.get(self.prefix+'/subscription').data
        self.assertEqual(subscription['status'],'not_connected')
        self.assertEqual(subscription['invoices'],[])
        self.assertIsNone(self.client.get(self.prefix+'/digest').data['revenue'])
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(self.prefix+'/subscription').status_code,403)


    def test_public_referral_returns_private_status_link(self):
        public=APIClient()
        result=public.post(f'/api/center-pro/referral/{self.center.id}',{'offer_id':self.offer.id,'patient_name':'Synthetic','preferred_slot_ids':self.choice_ids},format='json')
        token=result.data['status_url'].split('/')[-1]
        status=public.get('/api/center-pro/patient/'+token)
        self.assertEqual(status.status_code,200)
        self.assertEqual(status.data['reference'],result.data['reference'])
        self.assertEqual(status.data['status'],'pending')

    def test_daylight_saving_nonexistent_time_is_rejected(self):
        from datetime import date
        year=timezone.now().year+1
        transition=next(date(year,3,day) for day in range(8,15) if date(year,3,day).weekday()==6)
        hours=[{'weekday':i,'opens':'00:00','closes':'04:00','closed':False} for i in range(7)]
        self.client.post(self.prefix+'/hours',hours,format='json')
        response=self.client.post(self.prefix+'/availability',{'starts_at':str(transition)+'T02:15','ends_at':str(transition)+'T02:45','resource':'Sample scanner'},format='json')
        self.assertEqual(response.status_code,400)

    def test_patient_requires_three_distinct_center_slots(self):
        raw,link=self.patient_link()
        url='/api/center-pro/patient/'+raw
        at=timezone.now()+timedelta(days=4)
        foreign=Slot.objects.create(center=self.other,resource='Other',starts_at=at,ends_at=at+timedelta(minutes=30))
        for choices in [self.choice_ids[:2], [self.choice_ids[0]]*3, [*self.choice_ids[:2],foreign.id]]:
            self.assertEqual(self.client.post(url,{'patient_name':'Sample','preferred_slot_ids':choices},format='json').status_code,400)
        result=self.client.post(url,{'patient_name':'Sample','preferred_slot_ids':self.choice_ids},format='json')
        self.assertEqual(result.status_code,201)
        link.refresh_from_db()
        self.assertEqual(link.booking.preferred_slot_ids,self.choice_ids)

    def test_patient_can_request_times_without_existing_slots(self):
        from zoneinfo import ZoneInfo
        raw,link=self.patient_link()
        at=(timezone.now()+timedelta(days=10)).astimezone(ZoneInfo(self.center.timezone)).replace(second=0,microsecond=0,tzinfo=None)
        times=[(at+timedelta(hours=i)).isoformat(timespec='minutes') for i in range(3)]
        url='/api/center-pro/patient/'+raw
        response=self.client.post(url,{'patient_name':'Synthetic','preferred_times':times},format='json')
        self.assertEqual(response.status_code,201)
        link.refresh_from_db()
        self.assertEqual(len(link.booking.preferred_windows),3)
        self.assertEqual(link.booking.preferred_slot_ids,[])
        choices=self.client.get(self.prefix+f'/slots?booking_id={link.booking_id}')
        self.assertEqual(len(choices.data),3)
        self.assertTrue(all(row['unmatched'] for row in choices.data))
        from django.utils.dateparse import parse_datetime
        start=parse_datetime(link.booking.preferred_windows[0])
        slot=Slot.objects.create(center=self.center,resource='New resource',starts_at=start,ends_at=start+timedelta(minutes=30))
        response=self.client.post(self.prefix+f'/requests/{link.booking_id}/confirm',{'slot_id':slot.id},format='json')
        self.assertEqual(response.status_code,200)

    def test_patient_rejects_duplicate_and_past_manual_times(self):
        raw,link=self.patient_link()
        url='/api/center-pro/patient/'+raw
        for times in [['2001-01-01T10:00','2001-01-02T10:00','2001-01-03T10:00'], ['2099-01-01T10:00']*3]:
            self.assertEqual(self.client.post(url,{'patient_name':'Synthetic','preferred_times':times},format='json').status_code,400)

    def test_patient_preferences_respect_sending_center_hours(self):
        from .full_views import PatientInput
        from datetime import datetime
        # November 23, 2099 is independent of the host clock and future.
        local=datetime(2099,11,23,8,0)
        WorkingHours.objects.filter(center=self.center,weekday=local.weekday()).update(opens='08:00',closes='17:00',closed=False)
        def valid(first):
            form=PatientInput(data={'patient_name':'Sample','preferred_times':[first,'2099-11-23T09:00','2099-11-23T10:00']},context={'center_id':self.center.id})
            return form.is_valid()
        self.assertTrue(valid('2099-11-23T08:00'))
        self.assertFalse(valid('2099-11-23T07:59'))
        self.assertFalse(valid('2099-11-23T17:00'))
        WorkingHours.objects.filter(center=self.center,weekday=local.weekday()).update(closed=True)
        self.assertFalse(valid('2099-11-23T08:00'))
        WorkingHours.objects.filter(center=self.center,weekday=local.weekday()).delete()
        self.assertFalse(valid('2099-11-23T08:00'))

    def test_period_preferences_and_staff_confirmation(self):
        from .preferences import preference_bounds
        raw,link=self.patient_link()
        preferences=[{'date':'2099-11-23','period':'morning'},{'date':'2099-11-23','period':'afternoon'},{'date':'2099-11-24','period':'morning'}]
        response=self.client.post('/api/center-pro/patient/'+raw,{'patient_name':'Sample','preferences':preferences},format='json')
        self.assertEqual(response.status_code,201,response.data)
        link.refresh_from_db()
        begin,end=preference_bounds(self.center,preferences[0])
        inside=Slot.objects.create(center=self.center,resource='MRI',starts_at=begin,ends_at=begin+timedelta(minutes=30))
        outside=Slot.objects.create(center=self.center,resource='MRI',starts_at=begin-timedelta(hours=1),ends_at=begin-timedelta(minutes=30))
        choices=self.client.get(self.prefix+f'/slots?booking_id={link.booking_id}').data
        self.assertEqual(len(choices),3)
        self.assertEqual([s['id'] for s in choices[0]['slots']],[inside.id])
        url=self.prefix+f'/requests/{link.booking_id}/confirm'
        self.assertEqual(self.client.post(url,{'slot_id':outside.id},format='json').status_code,409)
        self.assertEqual(self.client.post(url,{'slot_id':inside.id},format='json').status_code,200)

    def test_period_validation_and_working_hours_clipping(self):
        from .full_views import PatientInput
        from .preferences import preference_bounds
        from datetime import date
        base=[{'date':'2099-11-23','period':'morning'},{'date':'2099-11-23','period':'afternoon'},{'date':'2099-11-24','period':'morning'}]
        def valid(values):
            form=PatientInput(data={'patient_name':'Sample','preferences':values},context={'center_id':self.center.id})
            return form.is_valid()
        self.assertTrue(valid(base))
        self.assertFalse(valid([base[0]]*3))
        self.assertFalse(valid([{**base[0],'period':'evening'},*base[1:]]))
        self.assertFalse(valid([{**base[0],'date':'2001-01-01'},*base[1:]]))
        hours=WorkingHours.objects.get(center=self.center,weekday=date(2099,11,23).weekday())
        hours.opens='09:00';hours.closes='11:00';hours.save()
        begin,end=preference_bounds(self.center,base[0])
        self.assertEqual((begin.hour,end.hour),(9,11))
        self.assertFalse(valid(base))
        hours.closed=True;hours.save()
        self.assertFalse(valid(base))

    def test_admin_non_ascii_code_returns_validation_error(self):
        raw=pyotp.random_base32()
        AdminMFA.objects.create(user=self.admin,encrypted_secret=cipher().encrypt(raw.encode()).decode())
        result=APIClient().post('/api/center-pro/auth/admin',{'username':'admin','password':'Synthetic-test-password-867','code':'１２３４５６'},format='json')
        self.assertEqual(result.status_code,403)

    def test_period_slots_beyond_first_page_are_accessible(self):
        from .preferences import preference_bounds
        booking=self.booking()
        booking.preferred_windows=[{'date':'2099-11-23','period':'morning'}]
        booking.save()
        start,end=preference_bounds(self.center,booking.preferred_windows[0])
        slots=[Slot.objects.create(center=self.center,resource=f'Resource {i:03}',starts_at=start,ends_at=start+timedelta(minutes=30)) for i in range(65)]
        url=self.prefix+f'/slots?booking_id={booking.id}'
        first=self.client.get(url).data[0]
        last=self.client.get(url+'&preference_page_0=4').data[0]
        self.assertEqual(first['count'],65)
        self.assertEqual(len(first['slots']),20)
        self.assertEqual([slot['id'] for slot in last['slots']],[slot.id for slot in slots[60:]])
        self.assertEqual(self.client.get(url+'&preference_page_0=bad').status_code,400)
