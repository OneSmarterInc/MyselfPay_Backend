import unittest
from unittest.mock import patch
import core
from search_results import apply_filters


class OptionalFilterTests(unittest.TestCase):
    def setUp(self):
        self.origin = {'kind': 'place', 'state': 'OH', 'latitude': 39.1, 'longitude': -84.5}
        self.entries = [dict(name='Ohio Center', booking_id='oh', states='OH', mri='Knee MRI without contrast', ct='Brain CT with contrast', locations=[dict(state='OH', latitude=39.1, longitude=-84.5, precision='city')]),
            dict(name='California Center', booking_id='ca', states='CA', mri='Available', ct='No', locations=[])]
        self.offers = [dict(id='knee', entry=self.entries[0], mod='MRI', contrast='without', exam_terms=['knee'], type='fixed', amount=300),
            dict(id='brain', entry=self.entries[0], mod='CT', contrast='with', exam_terms=['brain'], type='fixed', amount=150),
            dict(id='quote', entry=self.entries[1], mod='MRI', contrast='unknown', type='quote', amount=None)]

    def search(self, **filters):
        search = core.clean_search({'location':'National', **filters})
        national = search['location'] == 'National'
        result = dict(search=search, resolved_location=None if national else self.origin, location_suggestions=[])
        with patch('search_results.core.catalog', return_value={'offers':self.offers, 'entries':self.entries}):
            return apply_filters(result, 'unused', filters.get('sort', 'distance'), filters.get('scope'))

    def test_city_browse_includes_unpriced_city_records_without_other_filters(self):
        self.offers = []
        self.origin['name'] = 'Cincinnati'
        self.entries[0]['locations'][0]['city'] = 'Cincinnati'
        self.entries.append(dict(name='Unplaced Cincinnati', booking_id='city-no-coordinates', area='Cincinnati', states='OH', locations=[]))
        self.entries.append(dict(name='Dayton Center', booking_id='dayton', area='Dayton', states='OH', locations=[]))
        result = self.search(location='Cincinnati, OH', scope='city')
        self.assertEqual(result['search']['scope'], 'city')
        self.assertEqual({entry['name'] for entry in result['nearby_unpriced']}, {'Ohio Center','Unplaced Cincinnati'})

    def test_each_optional_filter_works_without_other_exam_fields(self):
        for filters, expected in [({}, {'knee','brain','quote'}),
                ({'mod':'CT'}, {'brain'}), ({'body':'Knee'}, {'knee','quote'}),
                ({'contrast':'with'}, {'brain'}), ({'budget':200}, {'brain'}),
                ({'type':'quote'}, {'quote'}), ({'type':'fixed'}, {'knee','brain'}),
                ({'mod':'MRI','contrast':'without','body':'Knee','budget':350,'type':'fixed'}, {'knee'})]:
            with self.subTest(filters=filters):
                # Offers without reviewed exam terms remain explicitly unconfirmed.
                self.assertEqual({offer['id'] for offer in self.search(**filters)['offers']}, expected)

    def test_radius_without_exam_and_price_sort_do_not_require_service(self):
        result = self.search(location='Cincinnati, OH', scope='radius', radius=25, sort='price')
        self.assertEqual([offer['id'] for offer in result['offers']], ['brain','knee'])
        self.assertEqual(result['search']['sort'], 'price')
        self.entries[0]['locations'][0]['latitude'] = 45
        self.assertEqual(self.search(location='Cincinnati, OH', scope='radius', radius=25)['offers'], [])

    def test_directory_leads_obey_price_and_exam_filters(self):
        self.offers = []
        self.assertEqual(len(self.search()['nearby_unpriced']), 2)
        self.assertEqual(len(self.search(mod='CT')['nearby_unpriced']), 1)
        self.assertEqual(len(self.search(body='Knee', contrast='without')['nearby_unpriced']), 1)
        self.assertEqual(self.search(contrast='both')['nearby_unpriced'], [])
        self.assertEqual(self.search(budget=500)['nearby_unpriced'], [])
        self.assertEqual(self.search(type='fixed')['nearby_unpriced'], [])
