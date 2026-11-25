from django.contrib.auth import get_user_model
from django.test import TestCase
from .test_helpers import OriginClient
from .models import Center,Membership,AuditEvent

class ManageStaffTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user(username='staff-owner',email='owner@example.test')
        self.staff=get_user_model().objects.create_user(username='existing-staff',email='staff@example.test',first_name='Existing staff')
        self.center=Center.objects.create(name='Owner center')
        self.other=Center.objects.create(name='Other center')
        self.ownership=Membership.objects.create(center=self.center,user=self.owner,role='owner')
        self.assignment=Membership.objects.create(center=self.center,user=self.staff,role='staff')
        self.client=OriginClient();self.client.force_authenticate(self.owner)
        self.url=f'/api/center-pro/centers/{self.center.pk}/staff'
    def add(self,email='new@example.test',**extra):
        return self.client.post(self.url,{'name':'New staff','email':email,**extra},format='json')
    def test_owner_adds_staff_with_unusable_password_and_audit(self):
        response=self.add('NEW@EXAMPLE.TEST',role='owner')
        self.assertEqual(response.status_code,201,response.data)
        member=Membership.objects.get(pk=response.data['id'])
        self.assertEqual(member.role,'staff')
        self.assertEqual(member.user.email,'new@example.test')
        self.assertFalse(member.user.has_usable_password())
        self.assertTrue(AuditEvent.objects.filter(center=self.center,actor=self.owner,action='access.assigned',reference=f'MEMBER-{member.pk}').exists())
    def test_owner_list_search_is_staff_only_and_center_scoped(self):
        other_user=get_user_model().objects.create_user(username='other-staff',email='other@example.test')
        Membership.objects.create(center=self.other,user=other_user,role='staff')
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['count'],1)
        self.assertEqual(response.data['results'][0]['email'],'staff@example.test')
        self.assertEqual(self.client.get(self.url,{'search':'EXISTING'}).data['count'],1)
        self.assertEqual(self.client.get(self.url,{'search':'other'}).data['count'],0)
    def test_duplicate_inactive_and_invalid_email_do_not_grant_access(self):
        self.assertEqual(self.add('staff@example.test').status_code,409)
        self.staff.is_active=False;self.staff.save()
        self.assertEqual(self.add('staff@example.test').status_code,400)
        self.assertEqual(self.add('invalid').status_code,400)
    def test_removal_preserves_account_and_other_center_assignment(self):
        other_membership=Membership.objects.create(center=self.other,user=self.staff,role='staff')
        response=self.client.post(f'{self.url}/{self.assignment.pk}/revoke',{},format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.assertFalse(Membership.objects.filter(pk=self.assignment.pk).exists())
        self.assertTrue(Membership.objects.filter(pk=other_membership.pk).exists())
        self.assertTrue(get_user_model().objects.get(pk=self.staff.pk).is_active)
        self.assertTrue(AuditEvent.objects.filter(action='access.revoked',actor=self.owner).exists())
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(f'/api/center-pro/centers/{self.center.pk}/overview').status_code,404)
    def test_staff_cannot_list_add_or_revoke_and_owner_cannot_remove_an_owner(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.assertEqual(self.add().status_code,403)
        self.assertEqual(self.client.post(f'{self.url}/{self.assignment.pk}/revoke',{},format='json').status_code,403)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(f'{self.url}/{self.ownership.pk}/revoke',{},format='json').status_code,404)
        self.assertTrue(Membership.objects.filter(pk=self.ownership.pk).exists())
        self.assertEqual(self.client.get(f'/api/center-pro/centers/{self.other.pk}/staff').status_code,404)
    def test_staff_from_another_center_cannot_be_revoked(self):
        other=Membership.objects.create(center=self.other,user=self.staff,role='staff')
        self.assertEqual(self.client.post(f'{self.url}/{other.pk}/revoke',{},format='json').status_code,404)
        self.assertTrue(Membership.objects.filter(pk=other.pk).exists())
    def test_cookie_authenticated_write_requires_csrf(self):
        client=OriginClient(enforce_csrf_checks=True);client.force_login(self.owner)
        session=client.session;session['center_pro_verified_user']=self.owner.pk;session.save()
        response=client.post(self.url,{'name':'Blocked','email':'blocked@example.test'},format='json')
        self.assertEqual(response.status_code,403)
        self.assertFalse(get_user_model().objects.filter(email='blocked@example.test').exists())
