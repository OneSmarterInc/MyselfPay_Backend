import csv
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import chr_arkansas
import chr_index

class ArkansasSourceTests(TestCase):
    def test_cpt_and_numeric_values_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ar.csv'
            with path.open('w',newline='',encoding='utf-8') as file:
                writer=csv.writer(file)
                writer.writerow(chr_arkansas.FIELDS)
                writer.writerow(['AR','Hospital','','0','12.345','1 to 9','07003'])
            row=list(chr_arkansas.source_rows(path))[0]
            self.assertEqual(row[7]['CPT code'],'07003')
            self.assertIsNone(row[3])
            self.assertEqual(str(row[4]),'0')
            self.assertEqual(str(row[5]),'12.345')
            self.assertEqual(chr_arkansas.validate_source(path)[:2],(1,1))
    def test_arkansas_dispatch(self):
        with patch('chr_arkansas.prices',return_value={'state':'AR'}) as prices:
            self.assertEqual(chr_index.compare({'state':'Arkansas','view':'hospitals'}),{'state':'AR'})
            prices.assert_called_once()
