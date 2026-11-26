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
  for module in ('california','north_dakota','kentucky','alabama','indiana','illinois','new_mexico','arkansas','colorado','hawaii','michigan','louisiana','pennsylvania','alaska','vermont','delaware_six'):
   with patch('chr_ranges.hospital_summary',return_value={'screened':True}) as summary,patch('chr_index.database_url',return_value='postgresql://localhost/selfpay_hospital_compare_v10'),patch('postgres_store.PostgresConnection'):
    result=importlib.import_module('chr_'+module).prices({'view':'hospitals','mri':'500'})
    self.assertEqual(result,{'screened':True});summary.assert_called_once()
