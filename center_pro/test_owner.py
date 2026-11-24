from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from .test_helpers import OriginClient as APIClient
from .models import Center, Membership, Offer, PriceChange, BookingRequest, OutreachTarget, OutreachActivity, AuditEvent


class OwnerModuleTests(TestCase):
    def setUp(self):
        self.client=APIClient()
        self.owner=get_user_model().objects.create_user(username='owner-a')
        self.second=get_user_model().objects.create_user(username='owner-b')
        self.staff=get_user_model().objects.create_user(username='staff')
        self.center=Center.objects.create(name='Sample center')
        self.other=Center.objects.create(name='Other center')
        for user,role in [(self.owner,'owner'),(self.second,'owner'),(self.staff,'staff')]:
            Membership.objects.create(center=self.center,user=user,role=role)
        self.offer=Offer.objects.create(center=self.center,exam='MRI',price=395)
        self.target=OutreachTarget.objects.create(center=self.center,name='Sample clinic',category='Clinic',distance_miles='2.4')
        self.prefix=f'/api/center-pro/centers/{self.center.id}'
        self.price_url=f'{self.prefix}/offers/{self.offer.id}'
        self.activity_url=f'{self.prefix}/outreach/{self.target.id}/activities'
        self.client.force_authenticate(self.owner)

    def change(self,price='400.00',version=1):
        return self.client.post(self.price_url,{'price':price,'expected_version':version,'reason':'Updated sample rate'},format='json')

    def test_price_change_keeps_booking_snapshot_and_records_actor_and_history(self):
        booking=BookingRequest.objects.create(center=self.center,reference='SAMPLE-1',patient_name='Sample patient',exam='MRI',price_snapshot=395)
        response=self.change()
        self.assertEqual(response.status_code,200)
        self.offer.refresh_from_db();booking.refresh_from_db()
        self.assertEqual(self.offer.price,Decimal('400.00'))
        self.assertEqual(self.offer.version,2)
        self.assertEqual(booking.price_snapshot,Decimal('395.00'))
        history=PriceChange.objects.get()
        self.assertEqual(history.old_price,Decimal('395.00'))
        self.assertEqual(history.actor,self.owner)
        self.assertEqual(AuditEvent.objects.get().action,'offer.price_changed')
        self.assertEqual(self.client.get(self.price_url+'/history').data['count'],1)

    def test_second_owner_has_equal_access_but_stale_version_is_rejected(self):
        self.assertEqual(self.change().status_code,200)
        self.client.force_authenticate(self.second)
        self.assertEqual(self.change('410.00',1).status_code,409)
        self.assertEqual(self.change('410.00',2).status_code,200)
        self.assertEqual(PriceChange.objects.count(),2)

    def test_staff_cannot_read_or_write_owner_modules(self):
        self.client.force_authenticate(self.staff)
        for path in ['/offers','/outreach',f'/offers/{self.offer.id}/history',f'/outreach/{self.target.id}/activities']:
            self.assertEqual(self.client.get(self.prefix+path).status_code,403)
        self.assertEqual(self.change().status_code,403)
        self.assertEqual(self.client.post(self.activity_url,{'kind':'visited','note':'Sample'},format='json').status_code,403)

    def test_cross_center_target_and_offer_are_not_accessible(self):
        offer=Offer.objects.create(center=self.other,exam='CT',price=450)
        target=OutreachTarget.objects.create(center=self.other,name='Other',category='Clinic',distance_miles=3)
        self.assertEqual(self.client.get(f'{self.prefix}/offers/{offer.id}').status_code,404)
        self.assertEqual(self.client.post(f'{self.prefix}/offers/{offer.id}',{'price':'500','expected_version':1,'reason':'Test'},format='json').status_code,404)
        self.assertEqual(self.client.post(f'{self.prefix}/outreach/{target.id}/activities',{'kind':'visited','note':'Test'},format='json').status_code,404)

    def test_invalid_price_and_empty_reason_do_not_write(self):
        for price in ['0','-2','1.999','100000000','395.00']:
            self.assertEqual(self.change(price).status_code,400)
        self.assertEqual(self.client.post(self.price_url,{'price':'400','expected_version':1,'reason':'  '},format='json').status_code,400)
        self.assertEqual(PriceChange.objects.count(),0)
        self.assertEqual(AuditEvent.objects.count(),0)

    def test_outreach_records_notes_actor_status_and_audit(self):
        response=self.client.post(self.activity_url,{'kind':'visited','note':'Discussed sample referral process.'},format='json')
        self.assertEqual(response.status_code,201)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status,'visited')
        self.assertEqual(OutreachActivity.objects.get().actor,self.owner)
        self.assertEqual(AuditEvent.objects.get().action,'outreach.visited')
        self.assertEqual(self.client.get(self.activity_url).data['count'],1)

    def test_outreach_input_validation_and_filters(self):
        for data in [{'kind':'send_email','note':'Test'},{'kind':'visited','note':'  '},{'kind':'visited','note':'x'*2001}]:
            self.assertEqual(self.client.post(self.activity_url,data,format='json').status_code,400)
        self.assertEqual(self.client.get(self.prefix+'/outreach?search=missing').data['count'],0)
        self.assertEqual(self.client.get(self.prefix+'/outreach?status=invalid').status_code,400)
        self.assertEqual(OutreachActivity.objects.count(),0)

    def test_history_pagination_caps_record_count(self):
        OutreachActivity.objects.bulk_create([OutreachActivity(target=self.target,actor=self.owner,kind='visited',note=f'Sample {i}') for i in range(27)])
        response=self.client.get(self.activity_url+'?page_size=100')
        self.assertEqual(response.data['count'],27)
        self.assertEqual(len(response.data['results']),20)
