import csv
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import chr_wyoming
import chr_index

class WyomingSourceTests(TestCase):
    def test_cpt_and_numeric_values_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ar.csv'
            with path.open('w',newline='',encoding='utf-8') as file:
                writer=csv.writer(file)
                writer.writerow(chr_wyoming.FIELDS)
                writer.writerow(['WY','Hospital','','0','12.345','1 to 9','07003'])
            row=list(chr_wyoming.source_rows(path))[0]
            self.assertEqual(row[7]['CPT code'],'07003')
            self.assertIsNone(row[3])
            self.assertEqual(str(row[4]),'0')
            self.assertEqual(str(row[5]),'12.345')
            self.assertEqual(chr_wyoming.validate_source(path)[:2],(1,1))
    def test_illinois_dispatch(self):
        with patch('chr_wyoming.prices',return_value={'state':'WY'}) as prices:
            self.assertEqual(chr_index.compare({'state':'Wyoming','view':'hospitals'}),{'state':'WY'})
            prices.assert_called_once()
