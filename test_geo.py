import unittest
from unittest.mock import patch
import core
import geo

class GeoTests(unittest.TestCase):
    def test_city_ambiguity_and_qualified(self):
        self.assertGreater(len(geo.lookup('Dayton')),1)
        r=geo.lookup('Dayton, OH');self.assertEqual(len(r),1);self.assertEqual(r[0]['state'],'OH')
        self.assertEqual(geo.lookup('dayton oh'),r)
    def test_lexington_common_name(self):
        r=geo.lookup('Lexington, KY')
        self.assertEqual(len(r),1)
        self.assertEqual(r[0]['match'],'exact')
        self.assertEqual(r[0]['state'],'KY')
        self.assertEqual(r[0]['latitude'],38.040678)
        self.assertGreater(len(geo.lookup('Lexington')),1)

    def test_zip_and_leading_zero(self):
        self.assertEqual(geo.lookup('45402')[0]['state'],'OH')
        self.assertEqual(geo.lookup('00601')[0]['zip'],'00601')
    def test_unknown_and_typo(self):
        self.assertEqual(geo.lookup('00000'),[])
        self.assertEqual(geo.lookup('4540'),[])
        self.assertEqual(geo.lookup('Cincinatti, OH')[0]['match'],'suggestion')
    def test_no_network(self):
        with patch('socket.socket',side_effect=AssertionError('Network forbidden')):
            geo._index.cache_clear();self.assertTrue(geo.lookup('Dayton, OH'))
    def test_cross_state_zip(self):
        zips=geo._index()[1];code=next(k for k,v in zips.items() if len(v)>1)
        self.assertGreater(len(geo.lookup(code)),1)
        for r in geo.lookup(code):self.assertEqual(len(geo.lookup(r['label'])),1)
    def test_shared_search(self):
        entry={'name':'Synthetic','states':'OH','area':'Dayton','location_type':'site','locations':[dict(city='Dayton',state='OH',latitude=39.78,longitude=-84.2,precision='city')]}
        offer={'id':'test','entry':entry,'mod':'MRI','type':'fixed','contrast':'without','amount':400,'region':'Dayton'}
        raw={'mod':'MRI','body':'Knee','location':'45402'}
        with patch.object(core,'catalog',return_value={'version':'synthetic','entries':[entry],'offers':[offer]}):
            result=core.search(raw);self.assertEqual(result['count'],1)
            self.assertIn('straight-line',result['location_notice']);self.assertEqual(result['resolved_location']['state'],'OH')
            for location in ['00000','Cincinatti, OH']:
                self.assertEqual(core.search(dict(raw,location=location))['count'],0)
            self.assertEqual(core.search(dict(raw,location='Dayton, OH'))['count'],1)
            self.assertEqual(core.agent('Find knee MRI near 45402 without contrast',{})['count'],1)
            self.assertEqual(core.search(dict(raw,location='Dayton, KY'))['count'],1)
            self.assertEqual(core.search(dict(raw,location='Lexington, KY'))['count'],1)
            self.assertEqual(core.agent('knee MRI near Lexington, KY',{})['count'],1)

class CityRankingTests(unittest.TestCase):
    def test_dayton_and_columbus_ohio_rank_first(self):
        for city in ['Dayton','Columbus']:
            origin=geo.lookup(city+', OH')[0]
            entry={'name':'Ohio facility','states':'OH','area':city,'location_type':'site','locations':[dict(origin,precision='city')]}
            with patch.object(core,'catalog',return_value={'version':'test','entries':[entry],'offers':[]}):
                result=core.search(dict(mod='MRI',body='brain',location=city,radius=25))
            self.assertEqual(result['resolved_location']['state'],'OH')
            self.assertFalse(result['location_suggestions'])

    def test_multiple_nearby_candidates_still_require_choice(self):
        entries=[]
        for place in ['Dayton, OH','Dayton, KY']:
            origin=geo.lookup(place)[0]
            # Kentucky is closer: population must still put Ohio first.
            entries.append({'name':place,'location_type':'site','locations':[dict(origin,latitude=origin['latitude']+(0.05 if origin['state']=='OH' else 0),precision='city')]})
        with patch.object(core,'catalog',return_value={'version':'test','entries':entries,'offers':[]}):
            result=core.search(dict(mod='MRI',body='brain',location='Dayton',radius=25))
        self.assertIsNone(result['resolved_location'])
        self.assertEqual(result['location_suggestions'][0],'Dayton, OH')

if __name__=='__main__':unittest.main()



class PopulationRankingTests(unittest.TestCase):
    def test_population_beats_facility_distance(self):
        for city, small_state in [('Dayton','KY'), ('Columbus','IN')]:
            entries=[]
            for state in ['OH',small_state]:
                origin=geo.lookup(f'{city}, {state}')[0]
                entries.append({'name':state,'location_type':'site','locations':[dict(origin,latitude=origin['latitude']+(0.1 if state=='OH' else 0),precision='city')]})
            with patch.object(core,'catalog',return_value={'version':'test','entries':entries,'offers':[]}):
                result=core.search(dict(mod='MRI',body='brain',location=city,radius=25))
            self.assertEqual(result['location_suggestions'][0],f'{city}, OH')
            self.assertIsNone(result['resolved_location'])

    def test_springfield_still_requires_state(self):
        entries=[]
        for state in ['OH','IL','MA']:
            origin=geo.lookup(f'Springfield, {state}')[0]
            entries.append({'name':state,'location_type':'site','locations':[dict(origin,precision='city')]})
        with patch.object(core,'catalog',return_value={'version':'test','entries':entries,'offers':[]}):
            result=core.search(dict(mod='MRI',body='brain',location='Springfield',radius=25))
        self.assertIsNone(result['resolved_location'])
        self.assertGreater(len(result['location_suggestions']),1)
        self.assertIn('Choose a location',result['location_notice'])

