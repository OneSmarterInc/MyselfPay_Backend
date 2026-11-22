import csv
import tempfile
import unittest
from pathlib import Path
import core
import hospital_rates

HEADER = ['cms_number', 'hospital_name', 'name_missing', 'zip', 'city', 'latitude', 'longitude', 'cpt', 'exam', 'cash_price',
          'gross_charge', 'negotiated_low', 'negotiated_high', 'payer_count', 'reading_negotiated_low', 'reading_negotiated_high',
          'reading_included', 'shared_list_id']


def row(cms, name, zip5, lat, lon, cash, gross, lo, hi, shared='', cpt='70551'):
    return [cms, name, 'false', zip5, 'Dayton', lat, lon, cpt, '', cash, gross, lo, hi, 5, '', '', 'Unknown', shared]


class HospitalRatesTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.dir.name) / 'test.sqlite3')
        self.addCleanup(self.dir.cleanup)
        core.initialize(self.path)
        with core.connect(self.path) as db:
            db.executescript((Path(core.__file__).parent / 'ops/hospital-rates.sql').read_text())
            csv_path = Path(self.dir.name) / 'rates.csv'
            with open(csv_path, 'w', newline='') as f:
                w = csv.writer(f)
                w.writerow(HEADER)
                w.writerow(row('360001', 'Near Hospital', '45429', '39.7', '-84.17', '1000', '2000', '200', '800', 'SL1'))
                w.writerow(row('360002', 'Same System Hospital', '45431', '39.72', '-84.05', '1000', '2000', '200', '810', 'SL1'))
                w.writerow(row('360003', 'Other Hospital', '45409', '39.73', '-84.19', '2200', '2200', '236', '700'))
                w.writerow(row('360004', '', '45404', '39.77', '-84.2', '1500', '2200', '230', '350'))
                w.writerow(row('360005', 'Far Hospital', '43210', '39.99', '-83.01', '900', '1800', '210', '600'))
                w.writerow(row('360006', 'No Price Hospital', '45342', '39.64', '-84.2', '', '', '', ''))
                w.writerow(row('360001', 'Near Hospital', '45429', '39.7', '-84.17', '900', '1500', '180', '700', 'SL1', cpt='70450'))
            self.count = hospital_rates.load_csv(db, str(csv_path), '2026-11')

    def nearby(self, **kw):
        with core.connect(self.path) as db:
            return hospital_rates.nearby(db, kw.get('zip', '45459'), kw.get('cpt', '70551'), kw.get('radius', 25))

    def test_load_counts_rows(self):
        self.assertEqual(self.count, 7)

    def test_nearest_first_and_radius(self):
        data = self.nearby()
        names = [h['hospital_name'] for h in data['hospitals']]
        self.assertEqual(names[0], 'Near Hospital')
        self.assertNotIn('Far Hospital', names)
        self.assertTrue(all(h['distance_miles'] <= 25 for h in data['hospitals']))

    def test_unnamed_hospitals_stay_hidden(self):
        self.assertNotIn('', [h['hospital_name'] for h in self.nearby()['hospitals']])

    def test_shared_list_counts_once(self):
        data = self.nearby()
        self.assertEqual(data['hospital_count'], 3)
        self.assertEqual(data['system_count'], 2)

    def test_ranges_ignore_blank_prices(self):
        data = self.nearby()
        self.assertEqual(data['ranges']['cash'], {'low': 1000.0, 'high': 2200.0, 'hospitals': 3})
        self.assertEqual(data['ranges']['insurer']['low'], 200.0)
        self.assertEqual(data['ranges']['insurer']['high'], 810.0)

    def test_other_exam_is_separate(self):
        data = self.nearby(cpt='70450')
        self.assertEqual(data['hospital_count'], 1)
        self.assertEqual(data['exam'], 'CT head without contrast')

    def test_wider_radius_adds_hospitals(self):
        wide = [h['hospital_name'] for h in self.nearby(radius=50)['hospitals']]
        self.assertNotIn('Far Hospital', [h['hospital_name'] for h in self.nearby(radius=25)['hospitals']])
        self.assertEqual(len(wide), len(self.nearby(radius=25)['hospitals']))

    def test_hospital_with_no_price_drops_out(self):
        self.assertNotIn('No Price Hospital', [h['hospital_name'] for h in self.nearby()['hospitals']])

    def test_bad_inputs(self):
        with core.connect(self.path) as db:
            with self.assertRaises(ValueError):
                hospital_rates.nearby(db, '4545', '70551')
            with self.assertRaises(ValueError):
                hospital_rates.nearby(db, '45459', '99999')
            with self.assertRaises(LookupError):
                hospital_rates.nearby(db, '00000', '70551')

    def test_reload_replaces_rows(self):
        with core.connect(self.path) as db:
            db.execute('DELETE FROM hospital_rates')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM hospital_rates').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
