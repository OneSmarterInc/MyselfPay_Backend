import unittest
from decimal import Decimal
from unittest.mock import MagicMock,patch
from chr_ranges import reference,hospital_summary,OHIO_QUERY

class CHRRangeScreenTests(unittest.TestCase):
 def test_mri_then_ct_then_100(self):
  for query,expected in [({'mri':'500','ct':'300'},('500','MRI')),({'mri':'0','ct':'300'},('300','CT')),({'mri':'','ct':''},('100','Default')),({'mri':'0','ct':'0'},('100','Default'))]:
   value,basis=reference(query);self.assertEqual((str(value),basis),expected)
  for value in ('NaN','-1','bad'):
   with self.assertRaises(ValueError):reference({'mri':value})
 def test_summary_is_parameterized_and_filters_each_minimum_only(self):
  db=MagicMock();db.execute.return_value.fetchall.return_value=[dict(hospital_name='Hospital',source_rows=3,cash_min=Decimal('501'),cash_max=Decimal('7005.83'),gross_min=Decimal('601'),gross_max=Decimal('14011.67'),insured_min=Decimal('701'),insured_max=Decimal('14011.67'))]
  result=hospital_summary(db,{'mri':'500','search':"x' OR 1=1"},'public.nm_hospital_imaging_prices','NM','test')
  sql,args=db.execute.call_args.args
  self.assertNotIn("x' OR",sql);self.assertEqual(args[-1],Decimal('500'))
  self.assertIn('cash_min IS NOT NULL AND gross_min IS NOT NULL AND insured_min IS NOT NULL',sql)
  self.assertIn('MAX(cash_price) AS cash_max',sql)
  self.assertEqual(result['hospitals'][0]['cash_max'],'7005.83')
  self.assertEqual(result['threshold_basis'],'MRI')
 def test_all_state_summaries_share_screen(self):
  import importlib
  for module in ('california','new_york','washington','new_hampshire','virginia','puerto_rico','south_carolina','new_jersey','idaho','nebraska','connecticut','american_samoa','georgia','iowa','wisconsin','montana','oklahoma','south_dakota','district_of_columbia','tennessee','rhode_island','north_dakota','kentucky','alabama','indiana','illinois','new_mexico','arkansas','colorado','hawaii','michigan','louisiana','pennsylvania','alaska','vermont','delaware_six'):
   with patch('chr_ranges.hospital_summary',return_value={'screened':True}) as summary,patch('chr_index.database_url',return_value='postgresql://localhost/selfpay_hospital_compare_v10'),patch('postgres_store.PostgresConnection'):
    result=importlib.import_module('chr_'+module).prices({'view':'hospitals','mri':'500'})
    self.assertEqual(result,{'screened':True});summary.assert_called_once()

 def test_north_dakota_and_kentucky_read_existing_legacy_tables(self):
  import importlib
  from unittest.mock import patch
  for module,canonical,legacy in [('north_dakota','public.nd_hospital_imaging_prices','public.ky_hospitnd_imaging_prices'),('kentucky','public.ky_hospital_imaging_prices','public.ky_hospitky_imaging_prices')]:
   for canonical_exists in (True,False):
    with patch('chr_index.database_url',return_value='postgresql://localhost/selfpay_hospital_compare_v10'),patch('postgres_store.PostgresConnection') as connection:
     db=connection.return_value.__enter__.return_value
     db.execute.return_value.fetchone.return_value={'canonical':canonical if canonical_exists else None,'legacy':legacy}
     db.execute.return_value.fetchall.return_value=[]
     result=importlib.import_module('chr_'+module).prices({'view':'hospitals','mri':'500'})
     self.assertEqual(result['hospital_count'],0)
     sql=db.execute.call_args.args[0]
     self.assertIn('FROM '+(canonical if canonical_exists else legacy),sql)

 def test_state_schema_tables_use_canonical_names(self):
  from pathlib import Path
  root=Path(__file__).resolve().parents[1]
  for state,file in [('NY','new-york'),('WA','washington'),('NH','new-hampshire'),('VA','virginia'),('PR','puerto-rico'),('SC','south-carolina'),('NJ','new-jersey'),('ID','idaho'),('NE','nebraska'),('CT','connecticut'),('AS','american-samoa'),('GA','georgia'),('IA','iowa'),('WI','wisconsin'),('MT','montana'),('OK','oklahoma'),('SD','south-dakota'),('DC','district-of-columbia'),('TN','tennessee'),('RI','rhode-island'),('ND','north-dakota'),('KY','kentucky')]:
   table='public.'+state.lower()+'_hospital_imaging_prices'
   ddl=(root/'ops'/f'chr-{file}.sql').read_text()
   self.assertIn('CREATE TABLE IF NOT EXISTS '+table+' (',ddl)
   importer=(root/'api'/'management'/'commands'/('import_chr_'+file.replace('-','_')+'.py')).read_text()
   self.assertIn('COPY '+table+'(',importer)
