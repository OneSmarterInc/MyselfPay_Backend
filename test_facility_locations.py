import json
import tempfile
import unittest
from pathlib import Path
import core
from facility_locations import enrich,validate_locations

class LocationTests(unittest.TestCase):
    def entry(self,area='Dayton',states='OH',**kw):
        return dict(name='Synthetic clinic',area=area,states=states,source='https://example.com',checked='2026-10-09',**kw)
    def test_city_and_zip(self):
        e,issues=enrich(self.entry());self.assertFalse(issues);self.assertEqual(e['locations'][0]['precision'],'city')
        e,issues=enrich(self.entry(zip='45402'));self.assertFalse(issues);self.assertEqual(e['locations'][0]['precision'],'zip')
    def test_multicity(self):
        e,issues=enrich(self.entry('Waterbury; Prospect; Southbury','CT'));self.assertEqual(len(e['locations']),1);self.assertEqual([x['input'] for x in issues],['Prospect','Southbury'])
    def test_ambiguous_and_conflict(self):
        for e in [self.entry('Dayton',''),self.entry('Unknown place','OH'),self.entry('Dayton','KY',zip='45402')]:
            out,issues=enrich(e);self.assertTrue(issues);self.assertEqual(out['locations'],[])
    def test_program(self):
        out,issues=enrich(self.entry('National','OH; KY'));self.assertEqual(out['location_type'],'program');self.assertEqual(out['locations'],[])
    def test_partial_and_structured(self):
        out,issues=enrich(self.entry('Dayton; Unknown place'));self.assertEqual(len(out['locations']),1);self.assertEqual(len(issues),1)
        out,issues=enrich(self.entry('', 'OH; KY',locations=[{'city':'Dayton','state':'OH'}]));self.assertFalse(issues);self.assertEqual(len(out['locations']),1)
    def test_validation(self):
        with self.assertRaises(ValueError):validate_locations({'location_type':'site'})
        with self.assertRaises(ValueError):enrich(self.entry(locations='bad'))
    def test_stage_report_and_idempotence(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);db=d/'test.db';core.initialize(db)
            source=d/'source.json';maps=d/'maps.json'
            source.write_text(json.dumps([self.entry('Dayton; Unknown place')]))
            maps.write_text('[]')
            r=core.stage_import(source,maps,db)
            self.assertEqual(r['location_count'],1);self.assertEqual(len(r['unplaced_entries']),1)
            self.assertEqual(core.stage_import(source,maps,db)['id'],r['id'])
            with core.connect(db) as con:
                e=json.loads(con.execute('SELECT payload FROM entries').fetchone()[0]);validate_locations(e)
                self.assertIsNone(core.active_batch(con))

if __name__=='__main__':unittest.main()

