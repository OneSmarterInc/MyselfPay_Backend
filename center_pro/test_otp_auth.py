from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from .test_helpers import OriginClient as APIClient
from .models import Center,Membership,CenterLoginOTP

@override_settings(DEBUG=True,CENTER_PRO_LOCAL_LINKS=True)
class CenterOTPTests(TestCase):
 def setUp(self):
  p=patch('center_portal.centers',return_value=[]);p.start();self.addCleanup(p.stop)
  self.user=get_user_model().objects.create_user(username='otp-staff',email='staff@example.test')
  self.center=Center.objects.create(name='OTP center')
  self.member=Membership.objects.create(user=self.user,center=self.center,role='staff')
  self.client=APIClient(enforce_csrf_checks=True)
  self.csrf=self.client.get('/api/center-pro/session?workspace=center').json()['csrf_token']
 def post(self,path,body,client=None):return (client or self.client).post('/api/center-pro/auth/otp/'+path+'?workspace=center',body,format='json',HTTP_X_CSRFTOKEN=self.csrf)
 def issue(self):
  response=self.post('request',{'email':' STAFF@example.test '});self.assertEqual(response.status_code,200);return response.json()
 def verify(self,data,code=None):return self.post('verify',{'challenge':data['challenge'],'code':code if code is not None else data['development_code'],'role':'owner'})
 def test_correct_code_signs_in_once_with_membership_role_and_rotates_session(self):
  data=self.issue();row=CenterLoginOTP.objects.get()
  self.assertNotEqual(row.code_digest,data['development_code']);self.assertNotIn('development_url',data)
  session=self.client.session;session['admin_totp_verified']='old';session.save();before=session.session_key
  response=self.verify(data);self.assertEqual(response.status_code,200)
  self.assertEqual(response.json()['centers'][0]['role'],'staff')
  self.assertEqual(self.client.session['center_pro_verified_user'],self.user.pk)
  self.assertNotIn('admin_totp_verified',self.client.session)
  self.assertNotEqual(self.client.session.session_key,before)
  self.csrf=response.json()['csrf_token']
  self.assertEqual(self.verify(data).status_code,400)
 def test_owner_role_is_preserved(self):
  self.member.role='owner';self.member.save()
  self.assertEqual(self.verify(self.issue()).json()['centers'][0]['role'],'owner')
 def test_five_wrong_codes_lock_challenge(self):
  data=self.issue();wrong='000000' if data['development_code']!='000000' else '111111'
  for _ in range(5):self.assertEqual(self.verify(data,wrong).status_code,400)
  self.assertEqual(self.verify(data).status_code,400)
  self.assertIsNotNone(CenterLoginOTP.objects.get().used_at)
 def test_expired_and_revoked_access_cannot_sign_in(self):
  data=self.issue();CenterLoginOTP.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
  self.assertEqual(self.verify(data).status_code,400)
  data=self.issue();self.member.delete();self.assertEqual(self.verify(data).status_code,400)
 def test_disabled_account_and_inactive_center_are_denied(self):
  data=self.issue();self.user.is_active=False;self.user.save();self.assertEqual(self.verify(data).status_code,400)
  self.user.is_active=True;self.user.save();data=self.issue();self.center.directory_active=False;self.center.save();self.assertEqual(self.verify(data).status_code,400)
 def test_resend_invalidates_older_code_and_preserves_leading_zero(self):
  old=self.issue()
  with patch('center_pro.otp_auth.secrets.randbelow',return_value=123):new=self.issue()
  self.assertEqual(new['development_code'],'000123');self.assertEqual(self.verify(old).status_code,400)
  self.assertEqual(self.verify(new).status_code,200)
 @override_settings(CENTER_PRO_LOCAL_LINKS=False)
 def test_mail_adapter_sends_only_code_and_unknown_email_stays_generic(self):
  with patch('booking_mail.send_message') as send:
   response=self.post('request',{'email':self.user.email});self.assertEqual(response.status_code,200)
   message,recipient=send.call_args.args
   self.assertEqual(recipient,self.user.email);self.assertIn('sign-in code',message['Subject'])
   self.assertNotIn('http',message.get_content());self.assertNotIn('development_code',response.json())
   unknown=self.post('request',{'email':'unknown@example.test'})
   self.assertEqual(set(response.json()),set(unknown.json()));self.assertEqual(response.json()['detail'],unknown.json()['detail'])
   self.assertEqual(send.call_count,1)
 def test_request_rate_limits_and_csrf(self):
  for _ in range(5):self.assertEqual(self.post('request',{'email':'missing@example.test'}).status_code,200)
  self.assertEqual(self.post('request',{'email':'missing@example.test'}).status_code,429)
  anonymous=APIClient(enforce_csrf_checks=True)
  for path in ('request','verify'):
   self.assertEqual(anonymous.post('/api/center-pro/auth/otp/'+path,{},format='json').status_code,403)
 @override_settings(CENTER_PRO_LOCAL_LINKS=False)
 def test_send_failure_cannot_leave_valid_code(self):
  import booking_mail
  with patch('booking_mail.send_message',side_effect=booking_mail.MailUnavailable('Mail unavailable')):
   self.assertEqual(self.post('request',{'email':self.user.email}).status_code,503)
  self.assertEqual(CenterLoginOTP.objects.count(),0)

