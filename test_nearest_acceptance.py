"""Checklist acceptance checks using repository facility/source records, offline."""
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import core
from facility_locations import enrich
from geo import lookup
from proximity import miles, covers

class NearestAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.db=Path(cls.temp.name)/'acceptance.db'
        core.initialize(cls.db)
        cls.report=core.stage_import('seed/directory.json','seed/mappings.json',cls.db)
        core.publish(cls.report['id'],cls.db)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def search(self,location,**kw):
        return core.search(dict(mod='MRI',body='knee',location=location,**kw),self.db)
    def test_real_dayton_facilities_from_city_and_zip(self):
        expected={'dir-58-mri-0','dir-59-mri-0','dir-63-mri-0','dir-64-mri-0'}
        for location in ['Kettering, OH','45402']:
            result=self.search(location)
            found={o['id'] for o in result['offers'] if o['distance_miles']<15}
            self.assertTrue(expected <= found,(location,found))
        agent=core.agent('knee MRI near Kettering, OH',{},self.db)
        self.assertEqual([o['id'] for o in agent['offers']],[o['id'] for o in self.search('Kettering, OH')['offers']])
    def test_springfield_requires_state(self):
        r=self.search('Springfield')
        self.assertFalse(r['offers']);self.assertIn('Springfield, OH',r['location_suggestions'])
        self.assertGreater(len(r['location_suggestions']),1)
    def test_250_mile_fallback(self):
        r=self.search('Hilo, HI',radius=250)
        self.assertEqual(len(r['offers']),3);self.assertTrue(r['fallback'])
        self.assertEqual(r['results_heading'],'Nothing within 250 miles. Nearest options:')
        distances=[o['distance_miles'] for o in r['offers']]
        self.assertTrue(all(d>250 for d in distances));self.assertEqual(distances,sorted(distances))
    def test_real_national_program_group(self):
        r=self.search('Kettering, OH')
        group=r['program_entries']+[o['entry'] for o in r['programs']]
        self.assertIn('RadiologyAssist',[e['name'] for e in group])
        self.assertTrue(all('distance_miles' not in e for e in group))
        self.assertFalse(any(o['entry']['name']=='RadiologyAssist' for o in r['offers']))
        self.assertFalse(covers({'states':'Multiple','coverage':'Use locator'},'OH'))
    def test_known_distance(self):
        self.assertLess(abs(miles(lookup('Dayton, OH')[0],lookup('Cincinnati, OH')[0])-48),3)
    def test_complete_search_no_network(self):
        with patch.object(socket,'socket',side_effect=AssertionError('Network forbidden')),patch.object(socket,'create_connection',side_effect=AssertionError('Network forbidden')),patch.object(socket,'getaddrinfo',side_effect=AssertionError('Network forbidden')):
            self.assertTrue(self.search('45402')['offers'])
            self.assertTrue(core.agent('knee MRI near Kettering, OH',{},self.db)['offers'])
    def test_zip_in_coverage_and_city_lists(self):
        e,issues=enrich(dict(name='Fixture',states='OH',area='Dayton',coverage='5692 Far Hills Ave, Dayton, OH 45429'))
        self.assertFalse(issues);self.assertEqual(e['locations'][0]['zip'],'45429')
        e,issues=enrich(dict(name='Fixture',states='AZ',area='Tucson, Sierra Vista and Green Valley'))
        self.assertFalse(issues);self.assertEqual(len(e['locations']),3)
        e,issues=enrich(dict(name='Fixture',states='OH; IN',area='Cincinnati, OH; Richmond, IN'))
        self.assertFalse(issues);self.assertEqual(len(e['locations']),2)
    def test_source_hash_guard_preserved(self):
        self.assertEqual(self.report['offer_count'],57);self.assertFalse(self.report['withheld_offers'])
        entries=json.loads(Path('seed/directory.json').read_text())
        target=json.loads(Path('seed/mappings.json').read_text())[0]['entry_name']
        next(e for e in entries if e['name']==target)['mri']='Changed price; must be reviewed'
        source=Path(self.temp.name)/'changed.json';source.write_text(json.dumps(entries))
        r=core.stage_import(source,'seed/mappings.json',self.db)
        self.assertTrue(r['withheld_offers'])
    def test_unavailable_and_duplicate_quote_leads(self):
        entry,_=enrich(dict(name='Unavailable fixture',states='OH',area='Dayton',mri='Not offered on reviewed site'))
        catalog=dict(version='fixture',offers=[],entries=[entry])
        with patch.object(core,'catalog',return_value=catalog):self.assertFalse(self.search('Dayton, OH')['nearby_unpriced'])
        for location in ['Dayton, OH','45402']:
            r=self.search(location)
            self.assertFalse({o['entry']['name'] for o in r['offers']} & {e['name'] for e in r['nearby_unpriced']})

if __name__=='__main__':unittest.main()

