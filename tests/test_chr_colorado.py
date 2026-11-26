import csv
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import chr_colorado
import chr_index

class ColoradoSourceTests(TestCase):
    def test_cpt_and_numeric_values_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'co.csv'
            with path.open('w',newline='',encoding='utf-8') as file:
                writer=csv.writer(file)
                writer.writerow(chr_colorado.FIELDS)
                writer.writerow(['CO','Hospital','','0','12.345','1 to 9','07003'])
            row=list(chr_colorado.source_rows(path))[0]
            self.assertEqual(row[7]['CPT code'],'07003')
            self.assertIsNone(row[3])
            self.assertEqual(str(row[4]),'0')
            self.assertEqual(str(row[5]),'12.345')
            self.assertEqual(chr_colorado.validate_source(path)[:2],(1,1))
    def test_colorado_dispatch(self):
        with patch('chr_colorado.prices',return_value={'state':'CO'}) as prices:
            self.assertEqual(chr_index.compare({'state':'Colorado','view':'hospitals'}),{'state':'CO'})
            prices.assert_called_once()
