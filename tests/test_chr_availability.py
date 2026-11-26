from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from django.core.cache import cache
from rest_framework.test import APIRequestFactory,force_authenticate
from api.chr_views import CHRAvailabilityView

class CHRAvailabilityTests(SimpleTestCase):
 def setUp(self):cache.clear()
 def request(self,items,authenticate=True):
  request=APIRequestFactory().post('/api/chr-index/availability',{'items':items},format='json',HTTP_HOST='localhost')
  if authenticate:force_authenticate(request,user=SimpleNamespace(is_authenticated=True))
  return CHRAvailabilityView.as_view()(request)
 def test_no_match_and_match_and_deduplication(self):
  query={'state':'NM','view':'hospitals','mri':'500'}
  with patch('chr_index.compare',side_effect=[{'hospital_count':0},{'hospital_count':1}]) as compare:
   response=self.request([{'id':'empty','query':query},{'id':'same','query':query},{'id':'match','query':{**query,'mri':'300'}}])
   self.assertEqual(compare.call_count,2)
   self.assertEqual(response.data['results'],[{'id':'empty','available':False},{'id':'same','available':False},{'id':'match','available':True}])
   self.request([{'id':'cached','query':query}]);self.assertEqual(compare.call_count,2)
 def test_error_is_unknown_and_not_cached_as_no_matches(self):
  item={'id':'retry','query':{'state':'NM','view':'hospitals'}}
  with patch('chr_index.compare',side_effect=RuntimeError('private connection details')):
   response=self.request([item]);self.assertIsNone(response.data['results'][0]['available'])
   self.assertNotIn('private',str(response.data))
  with patch('chr_index.compare',return_value={'hospital_count':1}) as compare:
   self.assertTrue(self.request([item]).data['results'][0]['available']);compare.assert_called_once()
 def test_invalid_location_disables_and_batch_is_bounded(self):
  with patch('chr_index.compare',side_effect=ValueError('Invalid ZIP')):
   self.assertFalse(self.request([{'id':'bad','query':{'zip':'bad'}}]).data['results'][0]['available'])
  self.assertEqual(self.request([{}]*25).status_code,400)
  self.assertEqual(self.request([{'id':'x','query':'bad'}]).status_code,400)
  self.assertIn(self.request([],False).status_code,(401,403))
