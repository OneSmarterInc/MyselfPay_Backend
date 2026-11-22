"""Hospital rates over HTTP: public read, synthetic rows only."""
import csv
import unittest
from pathlib import Path
import core
import hospital_rates
from tests import test_api as fixtures

HEADER = ['cms_number', 'hospital_name', 'name_missing', 'zip', 'city', 'latitude', 'longitude', 'cpt', 'exam', 'cash_price',
          'gross_charge', 'negotiated_low', 'negotiated_high', 'payer_count', 'reading_negotiated_low', 'reading_negotiated_high',
          'reading_included', 'shared_list_id']


class HospitalRatesAPITests(unittest.TestCase):
    setUp = fixtures.APITests.setUp
    tearDown = fixtures.APITests.tearDown

    def load(self):
        path = Path(self.temp.name) / 'rates.csv'
        with open(path, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(HEADER)
            w.writerow(['360001', 'Synthetic Hospital One', 'false', '45429', 'Kettering', '39.7', '-84.17', '70551', '', '1000', '2000', '200', '800', 5, '', '', 'Unknown', ''])
        with core.connect(self.db) as db:
            db.executescript((Path(core.__file__).parent / 'ops/hospital-rates.sql').read_text())
            hospital_rates.load_csv(db, str(path), '2026-11')

    def test_public_read_returns_hospitals_and_ranges(self):
        self.load()
        response = self.client.get('/api/hospital-rates?zip=45459&cpt=70551')
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data['hospital_count'], 1)
        self.assertEqual(data['ranges']['cash'], {'low': 1000.0, 'high': 1000.0, 'hospitals': 1})
        self.assertIn('radiologist', data['reading_note'])

    def test_bad_zip_and_unknown_zip(self):
        self.load()
        self.assertEqual(self.client.get('/api/hospital-rates?zip=4545&cpt=70551').status_code, 400)
        self.assertEqual(self.client.get('/api/hospital-rates?zip=45459&cpt=99999').status_code, 400)
        self.assertEqual(self.client.get('/api/hospital-rates?zip=00000&cpt=70551').status_code, 404)


if __name__ == '__main__':
    unittest.main()
