import csv,tempfile
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIRequestFactory,force_authenticate
from .models import Center,Membership,PreviousScanRecord
from .previous_scan_views import PreviousScansView
from .management.commands.prepare_previous_scans import FIELDS

class PreviousScansTests(TestCase):
 def setUp(self):
  self.user=get_user_model().objects.create_user(username='scan-owner')
  self.center=Center.objects.create(name='Scan center',directory_key='scan-center')
  self.member=Membership.objects.create(user=self.user,center=self.center,role='owner')
 def test_import_preserves_zip_zero_and_missing_counts_and_is_repeatable(self):
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder)/'scans.csv'
   with p.open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerow({**dict.fromkeys(FIELDS,''),'source_id':'scan-1','center_key':'scan-center','center_name':'Scan center','zip_code':'01002','scans_2022':'0','scans_2024':'399'})
   call_command('import_previous_scans',str(p),dry_run=True);self.assertEqual(PreviousScanRecord.objects.count(),0)
   call_command('import_previous_scans',str(p));call_command('import_previous_scans',str(p))
   row=PreviousScanRecord.objects.get();self.assertEqual(row.zip_code,'01002');self.assertEqual(row.scans_2022,0);self.assertIsNone(row.scans_2023)
 def test_only_owner_can_read_zip_history(self):
  PreviousScanRecord.objects.create(source_id='scan-1',center=self.center,center_name='Scan center',zip_code='01002',scans_2024=399)
  request=APIRequestFactory().get('/previous-scans',HTTP_HOST='localhost');force_authenticate(request,user=self.user)
  with patch('center_pro.integration.sync_requests'):
   response=PreviousScansView.as_view()(request,center_id=self.center.pk)
  self.assertEqual(response.status_code,200);self.assertEqual(response.data['results'][0]['scans_2024'],399)
  self.member.role='staff';self.member.save()
  request=APIRequestFactory().get('/previous-scans',HTTP_HOST='localhost');force_authenticate(request,user=self.user)
  with patch('center_pro.integration.sync_requests'):
   response=PreviousScansView.as_view()(request,center_id=self.center.pk)
  self.assertEqual(response.status_code,403)
