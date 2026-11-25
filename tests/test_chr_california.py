import csv,io,os,tempfile,unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch,MagicMock
from django.core.management import call_command
from chr_california import FIELDS,source_rows,validate_source,prices
import chr_index

class CaliforniaCHRTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'ca.csv'
  with self.path.open('w',encoding='utf-8',newline='') as f:
   writer=csv.writer(f);writer.writerow(FIELDS)
   for _ in range(2):writer.writerow(['CA','Synthetic CA Hospital','','1.234','','10 to 30'])
 def test_validation_preserves_duplicate_rows_precision_and_nulls(self):
  count,hospitals,digest=validate_source(self.path)
  self.assertEqual((count,hospitals),(2,1));self.assertEqual(len(digest),64)
  rows=list(source_rows(self.path));self.assertEqual([r[0] for r in rows],[2,3])
  self.assertEqual(rows[0][4],Decimal('1.234'));self.assertIsNone(rows[0][3]);self.assertIsNone(rows[0][5])
  self.assertEqual(rows[0][7]['Cash price'],'1.234')
 def test_invalid_state_and_numeric_value_are_rejected(self):
  for values in (['OH','Wrong state','','10','20',''],['CA','Bad price','','NaN','20','']):
   with self.path.open('w',newline='') as f:w=csv.writer(f);w.writerow(FIELDS);w.writerow(values)
   with self.assertRaises(ValueError):validate_source(self.path)
 def test_dry_run_never_connects_to_any_database(self):
  with patch('psycopg.connect') as connect:
   call_command('import_chr_california',str(self.path),dry_run=True,stdout=io.StringIO())
  connect.assert_not_called()
 def test_import_uses_copy_and_grants_reader_select_only(self):
  runtime='postgresql://runtime_reader@localhost/selfpay_hospital_compare_v10'
  with patch('chr_index.database_url',return_value=runtime),patch.dict(os.environ,{'CHR_IMPORT_DATABASE_URL':runtime}),patch('psycopg.connect') as connect:
   call_command('import_chr_california',str(self.path),stdout=io.StringIO())
  db=connect.return_value.__enter__.return_value
  copy=db.cursor.return_value.copy.return_value.__enter__.return_value
  self.assertEqual(copy.write_row.call_count,2)
  self.assertEqual(copy.write_row.call_args_list[0].args[0][4],Decimal('1.234'))
  statements=[str(call.args[0]) for call in db.execute.call_args_list]
  self.assertTrue(any('GRANT SELECT ON public.ca_hospital_imaging_prices' in value for value in statements))
  self.assertFalse(any('TRUNCATE' in value or 'DROP TABLE' in value for value in statements))
 def test_california_dispatch_has_no_invented_exam_threshold(self):
  with patch('chr_california.prices',return_value={'state':'CA'}) as read:
   self.assertEqual(chr_index.compare({'state':'CA'}),{'state':'CA'})
  read.assert_called_once_with({'state':'CA'})
 def test_read_is_bounded_and_search_is_parameterized(self):
  with patch('chr_index.database_url',return_value='postgresql://localhost/selfpay_hospital_compare_v10'),patch('postgres_store.PostgresConnection') as connection:
   db=connection.return_value.__enter__.return_value
   db.execute.return_value.fetchone.return_value={'count':1,'hospital_count':1}
   db.execute.return_value.fetchall.return_value=[{'id':2,'state':'CA','hospital':'Synthetic','distance_miles':None,'cash_price':Decimal('1.234'),'gross_charge':None,'insurer_rates':''}]
   result=prices({'search':"test' OR 1=1",'page':2,'page_size':10})
  self.assertEqual(result['results'][0]['cash_price'],'1.234')
  last=db.execute.call_args
  self.assertNotIn("test' OR",last.args[0]);self.assertEqual(last.args[1][-2:],(10,10))
  for query in ({'page':0},{'page_size':10000},{'page':'bad'}):
   with self.assertRaises(ValueError):prices(query)
