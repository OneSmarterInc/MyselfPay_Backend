import csv,io,tempfile,unittest,os
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch
from django.core.management import call_command
import chr_delaware,chr_index

class DelawareCHRTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.path=Path(self.temp.name)/'de.csv'
  row=dict.fromkeys(sorted(chr_delaware.REQUIRED),'')
  row.update({'State':'DE','Hospital':'Synthetic DE Hospital','Place ID':'DE-1','CCN':'080004','City':'Dover','Zip':'19901','Modality':'MRI','Contrast':'Without','CPT code':'70551','Setting':'outpatient','Billing class':'facility','professional':'0','Cash price':'500.125','Gross charge':'1000','Insurer rate min':'200','Insurer rate max':'400'})
  with self.path.open('w',newline='',encoding='utf-8-sig') as f:w=csv.DictWriter(f,fieldnames=list(row));w.writeheader();w.writerow(row)
 def test_source_preserves_identity_prices_protocol_and_zip_reference(self):
  count,hospitals,_=chr_delaware.validate_source(self.path);self.assertEqual((count,hospitals),(1,1))
  row=list(chr_delaware.source_rows(self.path))[0]
  self.assertEqual(row[3],'080004');self.assertEqual(row[6],'19901');self.assertEqual(row[8],'without');self.assertEqual(row[13],Decimal('500.125'))
  self.assertEqual((row[18],row[19]),chr_delaware.zip_references()['19901'])
 def test_import_is_explicit_and_dry_run_never_connects(self):
  with patch('psycopg.connect') as connect:call_command('import_chr_delaware',str(self.path),dry_run=True,stdout=io.StringIO())
  connect.assert_not_called()
  url='postgresql://reader@localhost/selfpay_hospital_compare_v10'
  with patch('chr_index.database_url',return_value=url),patch.dict(os.environ,{'CHR_IMPORT_DATABASE_URL':url}),patch('psycopg.connect') as connect:
   call_command('import_chr_delaware',str(self.path),stdout=io.StringIO())
  copy=connect.return_value.__enter__.return_value.cursor.return_value.copy.return_value.__enter__.return_value
  self.assertEqual(len(copy.write_row.call_args.args[0]),22)
 def test_zip_and_city_origins_and_invalid_references(self):
  self.assertEqual(chr_delaware.origin({'zip':'19901'})[0],'19901')
  self.assertTrue(chr_delaware.origin({'city':'Dover'})[2])
  for query in ({'zip':'45219'},{'city':'Unknown synthetic town'},{'latitude':'NaN','longitude':'1'}):
   with self.assertRaises(ValueError):chr_delaware.origin(query)
 def test_modalities_thresholds_distance_and_hospital_identity(self):
  lat,lng=chr_delaware.zip_references()['19901']
  base={'place_id':'DE-1','ccn':'080004','hospital_name':'Synthetic DE Hospital','city':'Dover','zip5':'19901','self_pay_min':Decimal('500'),'self_pay_max':Decimal('600'),'gross_charge':Decimal('1000'),'insured_min':Decimal('200'),'insured_max':Decimal('400'),'codes_priced':2,'latitude':lat,'longitude':lng}
  with patch('chr_index.database_url',return_value='postgresql://localhost/selfpay_hospital_compare_v10'),patch('postgres_store.PostgresConnection') as connect:
   connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value=[{**base,'exam':'MRI'},{**base,'exam':'CT'},{**base,'place_id':'DE-FAR','exam':'MRI','latitude':45,'longitude':-75}]
   result=chr_index.compare({'state':'DE','zip':'19901','mri':'400','ct':'500'})
  self.assertEqual(result['matches'],{'MRI':1,'CT':0});self.assertEqual(result['hospital_count'],1)
  self.assertEqual(result['hospitals'][0]['ccn'],'080004');self.assertEqual(result['hospitals'][0]['id'],'DE-1')
  self.assertEqual(result['hospitals'][0]['distance_miles'],0)
  self.assertIn("contrast='without'",chr_delaware.QUERY);self.assertIn("('outpatient','both')",chr_delaware.QUERY)
