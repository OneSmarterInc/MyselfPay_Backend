import unittest
from decimal import Decimal
import chr_index

class CHRTests(unittest.TestCase):
 def row(self,ccn='1',exam='MRI',price='500',lat=39.1,lng=-84.5):
  return dict(ccn=ccn,hospital_name='Hospital '+ccn,zip5='45219',city='Cincinnati',latitude=lat,longitude=lng,exam=exam,self_pay_median=Decimal(price) if price else None,self_pay_min=Decimal(price) if price else None,self_pay_max=Decimal('900'),gross_charge=Decimal('1000'),insured_min=Decimal('200'),insured_max=Decimal('800'),codes_priced=12)
 def test_zip_preserves_leading_zero(self):self.assertEqual(chr_index.parameters({'zip':'00501','mri':'400'})[0],'00501')
 def test_invalid_zip(self):
  for value in ['1234','123456','４５２１９','45219;DROP']:
   with self.assertRaises(ValueError):chr_index.parameters({'zip':value,'mri':'400'})
 def test_requires_one_price(self):
  with self.assertRaises(ValueError):chr_index.parameters({'zip':'45219'})
 def test_zero_threshold_is_allowed(self):self.assertEqual(chr_index.parameters({'zip':'45219','mri':'0','ct':'0'})[1]['CT'],Decimal('0'))
 def test_lowest_price_not_median_controls_inclusion(self):
  row=self.row(price='600');row['self_pay_min']=Decimal('300')
  self.assertEqual(chr_index.build_result([row],'45219',{'MRI':Decimal('400'),'CT':None})['hospital_count'],0)
 def test_invalid_money(self):
  for value in ['-1','NaN','Infinity','bad','1000000','0.001']:
   with self.assertRaises(ValueError):chr_index.parameters({'zip':'45219','mri':value})
 def test_equal_or_lower_price_is_excluded(self):
  result=chr_index.build_result([self.row(price='400'),self.row('2',price='399.99')],'45219',{'MRI':Decimal('400'),'CT':None})
  self.assertEqual(result['hospital_count'],0)
 def test_modalities_are_compared_independently(self):
  rows=[self.row(),self.row(exam='CT',price='250')]
  result=chr_index.build_result(rows,'45219',{'MRI':Decimal('400'),'CT':Decimal('300')})
  self.assertEqual(result['hospital_count'],1);self.assertEqual(result['matches'],{'MRI':1,'CT':0})
  self.assertEqual(result['hospitals'][0]['prices']['MRI']['difference'],100)
 def test_both_matching_services_share_one_hospital(self):
  result=chr_index.build_result([self.row(),self.row(exam='CT',price='350')],'45219',{'MRI':Decimal('400'),'CT':Decimal('300')})
  self.assertEqual(result['hospital_count'],1);self.assertEqual(result['matches'],{'MRI':1,'CT':1})
 def test_missing_median_is_not_substituted_with_maximum(self):
  result=chr_index.build_result([self.row(price=None)],'45219',{'MRI':Decimal('400'),'CT':None})
  self.assertEqual(result['hospital_count'],0)
 def test_distance_sort_and_missing_location(self):
  result=chr_index.build_result([self.row('2',lat=40),self.row('3',lat=None),self.row('1')],'45219',{'MRI':Decimal('400'),'CT':None},(39.1,-84.5))
  self.assertEqual([row['ccn'] for row in result['hospitals']],['1','2','3'])
 def test_preserves_decimal_cents(self):
  result=chr_index.build_result([self.row(price='400.01')],'45219',{'MRI':Decimal('400'),'CT':None})
  self.assertEqual(result['hospitals'][0]['prices']['MRI']['difference'],.01)

if __name__=='__main__':unittest.main()
