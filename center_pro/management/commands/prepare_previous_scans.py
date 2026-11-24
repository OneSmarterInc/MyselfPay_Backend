import csv,json,re,zipfile
import xml.etree.ElementTree as ET
from django.core.management.base import BaseCommand,CommandError
from center_pro.integration import DomainConnection
from center_pro.employers import resolve_center

FIELDS=['source_id','center_key','center_name','center_location','zip_code','scans_2022','scans_2023','scans_2024','source_status','source_note']
class Command(BaseCommand):
 help='Export the CasesByZip Centers sheet, preserving missing counts and leading ZIP zeroes.'
 def add_arguments(self,p):p.add_argument('workbook');p.add_argument('output')
 def handle(self,*args,**o):
  import core
  db=DomainConnection();centers=[]
  for r in db.execute('SELECT entry_key,payload FROM entries WHERE batch_id=?',(core.active_batch(db),)).fetchall():
   e=json.loads(r['payload']);centers.append({'key':r['entry_key'],'name':e.get('name',''),'location':' '.join(str(e.get(k,'')) for k in ('address','coverage','area'))})
  ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'};count=matched=0
  with zipfile.ZipFile(o['workbook']) as z,open(o['output'],'w',encoding='utf-8-sig',newline='') as out:
   strings=[''.join(e.itertext()) for e in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si',ns)] if 'xl/sharedStrings.xml' in z.namelist() else []
   w=csv.DictWriter(out,fieldnames=FIELDS);w.writeheader()
   with z.open('xl/worksheets/sheet1.xml') as stream:
    for _,row in ET.iterparse(stream,events=['end']):
     if row.tag!='{'+ns['m']+'}row':continue
     n=int(row.get('r'));values=['']*8
     for c in row.findall('m:c',ns):
      ref=re.match('[A-Z]+',c.get('r','A'))[0]
      if len(ref)!=1 or ref>'H':continue
      v=c.find('m:v',ns);inline=c.find('m:is',ns)
      text=(v.text or '') if v is not None else ''.join(inline.itertext()) if inline is not None else ''
      values[ord(ref)-65]=strings[int(text)] if c.get('t')=='s' and text else text
     if n>1 and values[0].strip():
      name,location,zip_code,y22,y23,y24,status,note=values
      zip_code=zip_code.strip()
      if zip_code.isdigit() and len(zip_code)<5:zip_code=zip_code.zfill(5)
      key=resolve_center(name,location+' '+zip_code,centers)
      w.writerow(dict(zip(FIELDS,[f'casesbyzip-v2-{n}',key,name,location,zip_code,y22,y23,y24,status,note])))
      count+=1;matched+=bool(key)
     row.clear()
  self.stdout.write(f'Exported {count} center/ZIP rows; {matched} matched centers. Missing counts preserved.')
