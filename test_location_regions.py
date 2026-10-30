import unittest
from unittest.mock import patch
import core
import geo


class RegionSearchTests(unittest.TestCase):
    def setUp(self):
        mi = dict(name='Michigan', state='MI', kind='state', label='Michigan', latitude=44, longitude=-85)
        il = dict(name='Illinois', state='IL', kind='state', label='Illinois', latitude=40, longitude=-89)
        chicago = dict(name='Chicago', state='IL', kind='place', label='Chicago, IL', latitude=41.88, longitude=-87.63)
        self.index = patch.object(geo, '_index', return_value=({'chicago': [chicago]}, {}, {'michigan': mi, 'mi': mi, 'illinois': il, 'il': il}))
        self.index.start()
        self.addCleanup(self.index.stop)

    def test_state_prefix_offers_confirmation(self):
        self.assertEqual(geo.lookup('mich')[0]['label'], 'Michigan')
        self.assertEqual(geo.lookup('mich')[0]['match'], 'suggestion')
        self.assertEqual(geo.lookup('Michigan')[0]['match'], 'exact')
        self.assertEqual(geo.lookup('IL')[0]['label'], 'Illinois')

    def test_suburbs_use_known_center_with_notice(self):
        for text in ['Chicago suburbs, IL', 'Chicago metro area IL', 'Chicago area, Illinois']:
            result = geo.lookup(text)
            self.assertEqual(result[0]['label'], 'Chicago, IL')
            self.assertEqual(result[0]['match'], 'exact')
            self.assertIn('city center', result[0]['notice'])
        self.assertEqual(geo.lookup('Chicago suburbs, MI'), [])
        self.assertEqual(geo.lookup('00000'), [])

    def test_catalog_replaces_area_label_and_includes_states(self):
        choices = dict(core.location_choices([{'states': 'IL', 'area': 'Chicago suburbs', 'locations': []}]))
        self.assertIn('Chicago, IL', choices)
        self.assertNotIn('Chicago suburbs, IL', choices)
        self.assertIn('Michigan', choices)
        self.assertIn('Illinois', choices)

    def test_large_enriched_catalog_does_not_repeat_lookup(self):
        entries = [
            dict(states='IL', area='Chicago suburbs', location_type='site',
                 locations=[dict(city='Chicago', state='IL')]),
            dict(states='IL', area='Unknown location', location_type='unresolved', locations=[]),
            dict(states='IL', area='Nationwide', location_type='program', locations=[]),
        ] * 1000
        with patch.object(geo, 'lookup', side_effect=AssertionError('Repeated geocoding')):
            choices = core.location_choices(entries)
        self.assertEqual(choices.count(('Chicago, IL', 'Chicago')), 1)
        self.assertIn(('Illinois', 'Illinois'), choices)

    def test_statewide_keeps_distant_and_unmapped_centers(self):
        def entry(name, state, lat=None):
            return dict(name=name, states=state, area=name, mri='Available', location_type='site', locations=[] if lat is None else [dict(state=state, latitude=lat, longitude=-87.63, precision='city')])
        near=entry('Near', 'IL', 41.9)
        far=entry('Far', 'IL', 37)
        unmapped=entry('Unmapped', 'IL')
        outside=entry('Outside', 'MI', 41.88)
        def offer(e):
            return dict(id=e['name'],entry=e,mod='MRI',type='fixed',contrast='without',amount=400,region=e['area'])
        data=dict(version='test',entries=[far,outside,unmapped,near],offers=[offer(e) for e in [far,outside,near]])
        with patch.object(core,'catalog',return_value=data):
            for location in ['Chicago, IL','Illinois']:
                result=core.search(dict(scope='state',mod='MRI',body='Knee',location=location,radius=25))
                self.assertEqual({o['id'] for o in result['offers']},{'Near','Far'})
                self.assertEqual([e['name'] for e in result['nearby_unpriced']],['Unmapped'])
                self.assertIn('Illinois',result['results_heading'])
                self.assertFalse(result['fallback'])
                if location.startswith('Chicago'):
                    self.assertEqual(result['offers'][0]['id'],'Near')
                    self.assertGreater(result['offers'][1]['distance_miles'],25)
            result=core.search(dict(scope='state',mod='CT',body='Knee',location='Chicago, IL'))
            self.assertEqual(result['offers'],[])
            self.assertEqual(result['nearby_unpriced'],[])
            result=core.search(dict(scope='state',mod='MRI',body='Knee',location='mich'))
            self.assertIsNone(result['resolved_location'])
            self.assertEqual(result['offers'],[])

    def test_state_only_search_and_optional_filters(self):
        center=dict(name='Illinois center',states='IL',area='Chicago',location_type='site',locations=[],mri='Available',ct='Available')
        other=dict(center,name='Michigan center',states='MI')
        unmapped=dict(center,name='Unmapped center')
        offers=[dict(id=mod,entry=center,mod=mod,type='fixed',contrast='without',amount=400,region='IL',exam_terms=['knee']) for mod in ['MRI','CT']]
        with patch.object(core,'catalog',return_value=dict(version='test',entries=[center,other,unmapped],offers=offers)):
            result=core.search(dict(scope='state',location='Illinois'))
            self.assertEqual({o['id'] for o in result['offers']},{'MRI','CT'})
            self.assertEqual([e['name'] for e in result['nearby_unpriced']],['Unmapped center'])
            result=core.search(dict(scope='state',location='Illinois',mod='MRI'))
            self.assertEqual([o['id'] for o in result['offers']],['MRI'])
            result=core.search(dict(scope='state',location='Illinois',body='shoulder'))
            self.assertEqual(result['offers'],[])
            result=core.search(dict(scope='state',location=''))
            self.assertEqual(result['offers'],[])
            self.assertEqual(result['nearby_unpriced'],[])

    def test_search_resolves_region_and_keeps_notice(self):
        with patch.object(core, 'catalog', return_value={'version': 'test', 'entries': [], 'offers': []}):
            result = core.search(dict(mod='MRI', body='Knee', location='Chicago suburbs, IL'))
            self.assertEqual(result['resolved_location']['label'], 'Chicago, IL')
            self.assertIn('city center', result['location_notice'])
            result = core.search(dict(mod='MRI', body='Knee', location='mich'))
            self.assertEqual(result['location_suggestions'], ['Michigan'])


if __name__ == '__main__':
    unittest.main()

