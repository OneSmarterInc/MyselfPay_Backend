import csv
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import chr_guam
import chr_index

class GuamSourceTests(TestCase):
    def test_cpt_and_numeric_values_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ar.csv'
            with path.open('w',newline='',encoding='utf-8') as file:
                writer=csv.writer(file)
                writer.writerow(chr_guam.FIELDS)
                writer.writerow(['GU','Hospital','','0','12.345','1 to 9','07003'])
            row=list(chr_guam.source_rows(path))[0]
            self.assertEqual(row[7]['CPT code'],'07003')
            self.assertIsNone(row[3])
            self.assertEqual(str(row[4]),'0')
            self.assertEqual(str(row[5]),'12.345')
            self.assertEqual(chr_guam.validate_source(path)[:2],(1,1))
    def test_illinois_dispatch(self):
        with patch('chr_guam.prices',return_value={'state':'GU'}) as prices:
            self.assertEqual(chr_index.compare({'state':'Guam','view':'hospitals'}),{'state':'GU'})
            prices.assert_called_once()
