import csv,tempfile
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIRequestFactory,force_authenticate
from .models import Center,Membership,EmployerDirectory,NearbyCompany
from .employer_views import NearbyCompaniesView
from .management.commands.prepare_employer_directory import FIELDS

class EmployerTests(TestCase):
 def setUp(self):
  self.center=Center.objects.create(name='Assigned center',directory_key='assigned')
  self.other=Center.objects.create(name='Other center',directory_key='other')
  self.user=get_user_model().objects.create_user(username='company-member')
  self.member=Membership.objects.create(user=self.user,center=self.center,role='owner')
 def test_import_is_repeatable_and_retains_unmapped_operators(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'companies.csv'
   with path.open('w',newline='',encoding='utf-8') as f:
    writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader()
    for source,key in [('one','assigned'),('two','')]:
     writer.writerow({**dict.fromkeys(FIELDS,''),'source_id':source,'operator_name':'Operator','center_key':key,'source_row':'16','company_name':'Company','distance_miles':'12.25','employee_count':'Approximately 200','verification_status':'As supplied'})
   call_command('import_employer_directory',str(path),dry_run=True,verbosity=0)
   self.assertEqual(NearbyCompany.objects.count(),0)
   call_command('import_employer_directory',str(path),verbosity=0)
   call_command('import_employer_directory',str(path),verbosity=0)
   self.assertEqual(NearbyCompany.objects.count(),2)
   self.assertEqual(EmployerDirectory.objects.filter(center__isnull=True).count(),1)
 def test_owner_and_staff_see_only_their_center_and_under_100_miles(self):
  group=EmployerDirectory.objects.create(source_id='a',operator_name='Assigned',center=self.center)
  other=EmployerDirectory.objects.create(source_id='b',operator_name='Other',center=self.other)
  for g,row,distance in [(group,1,25),(group,2,101),(other,3,5)]:NearbyCompany.objects.create(directory=g,source_row=row,company_name='Company',distance_miles=distance)
  for role in ['owner','staff']:
   self.member.role=role;self.member.save()
   request=APIRequestFactory().get('/companies',HTTP_HOST='localhost');force_authenticate(request,user=self.user)
   with patch('center_pro.integration.sync_requests'):
    response=NearbyCompaniesView.as_view()(request,center_id=self.center.pk)
   self.assertEqual(response.status_code,200);self.assertEqual(response.data['count'],1)
  request=APIRequestFactory().get('/companies',HTTP_HOST='localhost');force_authenticate(request,user=self.user)
  response=NearbyCompaniesView.as_view()(request,center_id=self.other.pk)
  self.assertEqual(response.status_code,404)
