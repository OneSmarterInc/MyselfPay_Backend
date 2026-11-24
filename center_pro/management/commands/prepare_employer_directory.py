import csv,json,re,zipfile
import xml.etree.ElementTree as ET
from django.core.management.base import BaseCommand,CommandError
from center_pro.integration import DomainConnection
from center_pro.employers import resolve_center

FIELDS=['source_id','operator_name','operator_address','operator_location','center_key','source_row','company_name','company_address','public_email','email_type','business_phone','employee_count','distance_miles','location_distance_status','verification_status','source_links']
class Command(BaseCommand):
    help='Convert the supplied operator-wise XLSX to a repeatable UTF-8 CSV import.'
    def add_arguments(self,p):p.add_argument('workbook');p.add_argument('output')
    def handle(self,*args,**o):
        import core
        db=DomainConnection();centers=[]
        for r in db.execute('SELECT entry_key,payload FROM entries WHERE batch_id=?',(core.active_batch(db),)).fetchall():
            e=json.loads(r['payload']);centers.append({'key':r['entry_key'],'name':e.get('name',''),'location':' '.join(str(e.get(k,'')) for k in ('coverage','address','area'))})
        ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        count=0;operators=set();mapped=set();meta={};in_companies=False
        with zipfile.ZipFile(o['workbook']) as z,open(o['output'],'w',encoding='utf-8-sig',newline='') as output:
            strings=[''.join(e.itertext()) for e in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si',ns)] if 'xl/sharedStrings.xml' in z.namelist() else []
            writer=csv.DictWriter(output,fieldnames=FIELDS);writer.writeheader()
            with z.open('xl/worksheets/sheet1.xml') as stream:
                for _,row in ET.iterparse(stream,events=['end']):
                    if row.tag!='{'+ns['m']+'}row':continue
                    values=['']*10
                    for cell in row.findall('m:c',ns):
                        ref=re.match(r'[A-Z]+',cell.get('r','A'))[0];column=0
                        for letter in ref:column=column*26+ord(letter)-64
                        if column>10:continue
                        v=cell.find('m:v',ns);inline=cell.find('m:is',ns)
                        text=(v.text or '') if v is not None else ''.join(inline.itertext()) if inline is not None else ''
                        values[column-1]=strings[int(text)] if cell.get('t')=='s' and text else text
                    first=values[0].strip();number=int(row.get('r'))
                    if first.startswith('OPERATOR #'):
                        meta={'source_id':'operator-'+str(number),'operator_name':first.split(':',1)[-1].strip()};in_companies=False
                    elif first=='Operator Name':meta['operator_name']=values[1].strip()
                    elif first in ('Complete Physical Address','Location Street Address','Address','Physical Address'):meta['operator_address']=values[1].strip()
                    elif first in ('City, State and ZIP','City, State & Region','City, State & ZIP','City / State / ZIP'):meta['operator_location']=values[1].strip()
                    elif first=='Company Name':
                        in_companies=True;meta['center_key']=resolve_center(meta.get('operator_name',''),meta.get('operator_location',''),centers)
                    elif in_companies and first and not first.upper().startswith('COMPANIES WITHIN'):
                        if not meta.get('operator_name'):raise CommandError('Company row has no operator at row '+str(number))
                        record={**meta,'source_row':number,**dict(zip(FIELDS[6:],values))}
                        writer.writerow(record);count+=1;operators.add(meta['source_id'])
                        if meta.get('center_key'):mapped.add(meta['source_id'])
                    row.clear()
        self.stdout.write(f'Exported {count} company rows for {len(operators)} operators; {len(mapped)} operators matched uniquely. Unmatched center_key values remain blank for review.')
