import unittest
from unittest.mock import patch
import core
from proximity import miles,offer_locations

class SearchTests(unittest.TestCase):
    def loc(self,lat=39.784743,lon=-84.199632):return dict(city='Dayton',state='OH',latitude=lat,longitude=lon,precision='city')
    def entry(self,name='A',**kw):return dict(name=name,states='OH',area='Dayton',mri='Call for quote',locations=[self.loc()],location_type='site',**kw)
    def offer(self,id,lat=39.784743):
        e=self.entry(id);e['locations']=[self.loc(lat)]
        return dict(id=id,entry=e,mod='MRI',contrast='without',type='fixed',amount=400,region='Dayton')
    def run_search(self,offers,entries=None,**kw):
        with patch.object(core,'catalog',return_value=dict(version='test',offers=offers,entries=entries or [o['entry'] for o in offers])):
            return core.search(dict(mod='MRI',body='knee',location='Dayton, OH',**kw))
    def test_radius_sort_and_cross_state(self):
        a=self.offer('near');b=self.offer('far',40.5);c=self.offer('cross',39.9);c['entry']['states']='KY'
        r=self.run_search([b,c,a],radius=25);self.assertEqual([o['id'] for o in r['offers']],['near','cross'])
        self.assertEqual(self.run_search([b,a],radius=100)['count'],2)
    def test_three_nearest(self):
        r=self.run_search([self.offer(str(i),42+i) for i in range(5)])
        self.assertEqual(r['count'],3);self.assertTrue(r['fallback']);self.assertEqual(r['results_heading'],'Nothing within 50 miles. Nearest options:')
    def test_program_and_quote(self):
        a=self.offer('p');a['entry']['location_type']='program';a['entry']['locations']=[]
        q=self.entry('quote');r=self.run_search([a],[a['entry'],q])
        self.assertEqual(len(r['programs']),1);self.assertNotIn('distance_miles',r['programs'][0]);self.assertEqual(len(r['nearby_unpriced']),1)
    def test_unknown_and_missing(self):
        a=self.offer('a');a['entry']['locations']=[]
        self.assertEqual(self.run_search([a])['unlocated_offer_count'],1)
        with patch.object(core,'catalog',return_value=dict(version='test',offers=[a],entries=[a['entry']])):
            r=core.search(dict(mod='MRI',body='knee',location='zzzzzzzz'))
            self.assertIn('ZIP',r['location_notice']);self.assertFalse(r['offers'])
    def test_scope_and_distance(self):
        a=self.offer('a');a['entry']['locations'] += [dict(self.loc(),city='Cincinnati')]
        self.assertEqual(len(offer_locations(a)),1)
        self.assertAlmostEqual(miles(self.loc(),self.loc()),0)
        self.assertAlmostEqual(miles(dict(latitude=0,longitude=0),dict(latitude=1,longitude=0)),69.09,places=1)
    def test_quote_fallback_outside_radius(self):
        entries=[self.entry(str(i)) for i in range(4)]
        for i,e in enumerate(entries):e['locations']=[self.loc(42+i)]
        r=self.run_search([],entries)
        self.assertEqual([e['name'] for e in r['nearby_unpriced']],['0','1','2'])
        self.assertIn('outside your radius',r['location_notice'])

    def test_price_filter_fallback_preserves_search(self):
        r=self.run_search([self.offer('a')],type='range',budget=100)
        self.assertEqual(r['offers'][0]['id'],'a')
        self.assertEqual(r['search']['type'],'range')
        self.assertIn('Above',r['offers'][0]['budget_status'])
        self.assertIn('Different price type: fixed',r['offers'][0]['filter_differences'])

    def test_mismatched_procedure_never_becomes_an_alternative(self):
        for changes in [dict(mod='CT'), dict(exam_terms=['brain']), dict(contrast='without'), dict(contrast='unknown')]:
            a=self.offer('a');a.update(contrast='with');a.update(changes)
            r=self.run_search([a],type='range',contrast='with')
            self.assertFalse(r['offers'])
            self.assertTrue(r['no_exam_price'])
            self.assertIn('No listed facility publishes a price for this exam',r['location_notice'])

    def test_brain_with_contrast_never_returns_ct_or_without(self):
        candidates=[self.offer('ct'),self.offer('without'),self.offer('unknown')]
        for offer in candidates:offer['exam_terms']=['brain']
        candidates[0].update(mod='CT',contrast='with')
        candidates[2]['contrast']='unknown'
        with patch.object(core,'catalog',return_value=dict(version='test',offers=candidates,entries=[o['entry'] for o in candidates])):
            r=core.search(dict(mod='MRI',body='brain',contrast='with',location='Dayton, OH'))
        self.assertFalse(r['offers'])
        self.assertTrue(r['nearby_unpriced'])

    def test_alternative_prefers_service_then_nearest(self):
        a=self.offer('ct');a['mod']='CT'
        b=self.offer('mri',42);c=self.offer('far',44)
        r=self.run_search([c,a,b],type='range')
        self.assertEqual(r['offers'][0]['id'],'mri')
        self.assertIn('Outside selected radius',r['offers'][0]['filter_differences'])

    def test_alternative_across_locations_and_chat(self):
        a=self.offer('mri')
        data=dict(version='test',offers=[a],entries=[a['entry']])
        with patch.object(core,'catalog',return_value=data):
            for location in ['Lexington, KY','Boise, ID','45402','Kettering, OH']:
                r=core.search(dict(mod='MRI',body='knee',location=location))
                self.assertEqual(r['count'],1)
                self.assertIn('distance_miles',r['offers'][0])
            r=core.agent('knee MRI near Boise, ID',{})
            self.assertEqual(r['offers'][0]['mod'],'MRI')
            self.assertTrue(r['fallback'])

    def test_baltimore_choice_then_nationwide_nearest(self):
        a=self.offer('dayton-only')
        with patch.object(core,'catalog',return_value=dict(version='test',offers=[a],entries=[a['entry']])):
            raw=dict(mod='MRI',body='knee',location='Baltimore',radius=25)
            r=core.search(raw)
            self.assertEqual(set(r['location_suggestions']),{'Baltimore, MD','Baltimore, OH'})
            self.assertFalse(r['offers'])
            for city in r['location_suggestions']:
                found=core.search(dict(raw,location=city))
                self.assertEqual(found['offers'][0]['id'],'dayton-only')
                self.assertGreater(found['offers'][0]['distance_miles'],25)
                self.assertTrue(found['fallback'])

    def test_agent_uses_quote_fallback(self):
        e=self.entry('far');e['locations']=[self.loc(45)]
        with patch.object(core,'catalog',return_value=dict(version='test',offers=[],entries=[e])):
            r=core.agent('knee MRI near Kettering, OH',{})
        self.assertEqual(r['nearby_unpriced'][0]['name'],'far')
        self.assertIn('outside your radius',r['reply'])

    def test_agent_and_radius_validation(self):
        a=self.offer('a')
        with patch.object(core,'catalog',return_value=dict(version='test',offers=[a],entries=[a['entry']])):
            r=core.agent('knee MRI near Kettering, OH',{})
            self.assertEqual(r['offers'][0]['id'],'a');self.assertIn('distance_miles',r['offers'][0])
        with self.assertRaises(ValueError):self.run_search([],radius=5)

if __name__=='__main__':unittest.main()



