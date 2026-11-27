"""Lossless workbook preparation and atomic upsert into the main directory."""
import csv
import json
import re
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
import core
from facility_locations import enrich
from geo import lookup

FIELDS = {'name':'A','area':'B','states':'C','entity':'D','category':'E','mri':'F','ct':'G','other':'H','coverage':'I','notes':'J','additional':'L','checked':'M','record_scope':'N','email':'O','email_scope':'P','email_source':'Q','email_checked':'R','phone':'S','phone_scope':'T','phone_source':'U','phone_checked':'V','phone_evidence_status':'W','record_category':'X','source_record_id':'Y','source_record':'Z','verification_status':'AA','address':'AC','current_offer_scope':'AH','zip_evidence':'BC','zip_verification':'BB','zip_review':'BD'}
STATE_CODES=set('AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY AS GU MP PR VI'.split())

def workbook_rows(filename):
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(filename) as z:
        strings=[''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si',ns)] if 'xl/sharedStrings.xml' in z.namelist() else []
        rows=ET.fromstring(z.read('xl/worksheets/sheet1.xml')).findall('s:sheetData/s:row',ns)
        header=next((r for r in rows if any(c.find('s:v',ns) is not None and c.get('t')=='s' and strings[int(c.find('s:v',ns).text)]=='Stable source record ID' for c in r)),None)
        if header is None:raise ValueError('Workbook header not found')
        for r in rows:
            if int(r.get('r'))<=int(header.get('r')):continue
            item={}
            for c in r:
                col=re.sub(r'\d','',c.get('r'));v=c.find('s:v',ns)
                text=strings[int(v.text)] if c.get('t')=='s' and v is not None else ''.join(c.find('s:is',ns).itertext()) if c.get('t')=='inlineStr' else v.text if v is not None else ''
                item[col]=text or ''
            if any(item.values()):yield item

def first_url(text):
    match=re.search(r'https?://[^\s<>"|]+',text)
    return match.group().rstrip('.,;)') if match else ''

def normalize(raw):
    entry={field:str(raw.get(col,'')).strip() for field,col in FIELDS.items()}
    entry['name']=entry['name'] or raw.get('AM','').strip()
    entry['area']=entry['area'] or raw.get('AN','').strip()
    states=list(dict.fromkeys(s.strip().upper() for s in re.split('[;,/|]',entry['states'] or raw.get('AO','')) if s.strip().upper() in STATE_CODES))
    if not states and entry['states'].casefold() in ('multiple','multistate','multi-state'):
        states=sorted({c['state'] for z in re.findall(r'(?<!\d)\d{5}(?!\d)',raw.get('BA','')) for c in lookup(z) if c['kind']=='zcta' and c['match']=='exact'})
    entry['states']='; '.join(states) or entry['states']
    entry['source']=first_url(raw.get('K','')) or first_url(raw.get('AJ','')) or first_url(raw.get('AT',''))
    entry['website']=entry['source']
    entry['email']=raw.get('AE','').strip() or entry['email'] or raw.get('AV','').strip()
    entry['email_scope']=raw.get('AF','').strip() or entry['email_scope']
    entry['phone']=raw.get('AD','').strip() or entry['phone'] or raw.get('AR','').strip()
    entry['address']=entry['address'] or raw.get('AP','').strip()
    zips=list(dict.fromkeys(re.findall(r'(?<!\d)\d{5}(?!\d)',raw.get('BA',''))))
    entry['zip_codes']='; '.join(zips)
    # A grouped operator does not have one primary ZIP. Pair each ZIP by Census
    # state, never by its position in the independent source lists.
    entry['zip']=zips[0] if len(zips)==1 and len(states)==1 else ''
    locations=[]
    for zipcode in zips:
        choices=[c for c in lookup(zipcode) if c['kind']=='zcta' and c['match']=='exact' and c['state'] in states]
        if len(choices)==1:locations.append({'city':'', 'state':choices[0]['state'],'zip':zipcode})
    entry['locations']=locations
    if re.search(r'\bnationwide\b',entry['coverage'],re.I):entry['location_type']='program'
    entry,unresolved=enrich(entry)
    entry['location_review']=unresolved
    entry['source_fields']=raw
    for service in ('mri','ct'):
        entry[service]=re.split(r'\n\nSUPPLIED FINDINGS',entry[service],maxsplit=1)[0].strip()
    entry['workbook_prices']=True
    # Preserve the workbook's full wording; these are source prices, not new
    # confirmed bookable offers or a price applicable to every branch.
    entry['price_verification']='unverified' if re.search(r'unverified|historical|not fully rechecked|not independently', ' '.join(raw.get(c,'') for c in ['F','G','H','AA','AH']),re.I) else 'source-reported'
    return entry

def prepare(filename, output):
    rows=[]
    for raw in workbook_rows(filename):
        entry=normalize(raw)
        reason='Archived promotion: already represented in the main directory' if 'Archive' in entry['record_category'] else 'Missing center name' if not entry['name'] else ''
        row={k:v for k,v in entry.items() if k not in ('locations','source_fields','location_review')}
        row.update(import_action='skip' if reason else 'upsert',skip_reason=reason,locations_json=core.canonical(entry['locations']),source_json=core.canonical(raw))
        states=[s.strip() for s in entry['states'].split(';') if s.strip() in STATE_CODES]
        row.update({f'state_{i+1}':s for i,s in enumerate(states)})
        rows.append(row)
    columns=list(FIELDS)+['source','website','zip','zip_codes','price_verification','location_type','import_action','skip_reason','locations_json','source_json']+[f'state_{i}' for i in range(1,17)]
    with open(output,'w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    return {'source_rows':len(rows),'upsert_rows':sum(r['import_action']=='upsert' for r in rows),'skipped_rows':sum(r['import_action']=='skip' for r in rows)}

def read_csv(filename):
    csv.field_size_limit(10000000)
    result=[];seen=set()
    with open(filename,encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            ident=row.get('source_record_id','').strip()
            if not ident or ident in seen:raise ValueError('Missing or duplicate source record ID: '+ident)
            seen.add(ident)
            if row.get('import_action')=='skip':continue
            raw=json.loads(row['source_json']);entry={key:row.get(key,'') for key in FIELDS};entry.update(source_fields=raw,workbook_prices=True)
            # Normalized CSV fields are editable; retain original evidence separately.
            for key in FIELDS.keys() | {'source','website','zip','zip_codes','price_verification'}:entry[key]=row.get(key,entry.get(key,''))
            if not entry['name'].strip():raise ValueError('Missing center name: '+ident)
            if not entry['source_record_id']==ident:raise ValueError('Conflicting source ID')
            entry['locations']=json.loads(row['locations_json']);entry['location_type']=row['location_type']
            entry,_=enrich(entry)
            result.append(entry)
    return sorted(result,key=lambda e:e['record_category']!='Main')

def upsert(filename, path, dry_run=True, replace_existing=False):
    incoming=read_csv(filename)
    if replace_existing and not incoming:raise ValueError('Cannot replace the directory with an empty import')
    with core.connect(path) as db:
        if not dry_run:
            # Serialize publication against other directory publications.
            if str(path).startswith(('postgres://','postgresql://')):db.execute("SELECT value FROM settings WHERE key='active_batch' FOR UPDATE").fetchone()
            else:db.execute('BEGIN IMMEDIATE')
        active=core.active_batch(db)
        if not active:raise ValueError('Main database has no active directory batch')
        old={r['entry_key']:json.loads(r['payload']) for r in db.execute('SELECT entry_key,payload FROM entries WHERE batch_id=?',(active,)).fetchall()}
        entries=dict(old);by_id={};by_name={}
        for key,e in entries.items():
            for ident in [e.get('source_record_id')]+e.get('source_record_aliases',[]):
                if ident:
                    if ident in by_id and by_id[ident]!=key:raise ValueError('Ambiguous existing source ID: '+ident)
                    by_id[ident]=key
            by_name.setdefault((e['name'].strip().casefold(),e.get('states','').replace(' ','')),[]).append(key)
        changed={};processed=set();added=updated=aliases=0
        for entry in incoming:
            ident=entry['source_record_id'];key=by_id.get(ident)
            if key is None:
                candidates=by_name.get((entry['name'].strip().casefold(),entry['states'].replace(' ','')),[])
                candidates=[k for k in candidates if entries[k].get('zip_codes',entries[k].get('zip',''))==entry['zip_codes'] and entry['zip_codes']]
                if len(candidates)>1:raise ValueError('Ambiguous center match: '+ident)
                key=candidates[0] if candidates else None
            if key is None:
                key=core.digest({'directory_source_id':ident});added+=1
            elif key not in processed:updated+=1
            previous=entries.get(key,{})
            if key in processed:
                entry={**previous,'source_record_aliases':sorted(set(previous.get('source_record_aliases',[])+[ident])),'supporting_source_records':{**previous.get('supporting_source_records',{}),ident:entry['source_fields']}}
                aliases+=1
            else:
                if previous.get('source_record_id') and previous['source_record_id']!=ident:
                    entry['source_record_id']=previous['source_record_id']
                    entry['source_record_aliases']=sorted(set(previous.get('source_record_aliases',[])+[ident]))
                elif previous.get('source_record_aliases'):entry['source_record_aliases']=previous['source_record_aliases']
                if previous.get('supporting_source_records'):entry['supporting_source_records']=previous['supporting_source_records']
            entries[key]=entry;by_id[ident]=key;processed.add(key)
            by_name.setdefault((entry['name'].strip().casefold(),entry['states'].replace(' ','')),[])
            if key not in by_name[(entry['name'].strip().casefold(),entry['states'].replace(' ',''))]:by_name[(entry['name'].strip().casefold(),entry['states'].replace(' ',''))].append(key)
        removed=set(old)-processed if replace_existing else set()
        if replace_existing:entries={k:e for k,e in entries.items() if k in processed}
        changed={k:e for k,e in entries.items() if old.get(k)!=e}
        report={'parent_batch':active,'incoming_records':len(incoming),'matched_centers':updated,'new_centers':added,'supporting_alias_records':aliases,'changed_entries':len(changed),'replace_existing':replace_existing,'removed_unmatched_centers':len(removed),'preserved_unmatched_centers':0 if replace_existing else len(set(old)-processed),'total_centers':len(entries),'dry_run':dry_run}
        if dry_run or (not changed and not removed):return report
        batch=str(uuid.uuid4())
        db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)',(batch,core.digest({'parent':active,'entries':entries}),Path(filename).name,time.time(),'staged','{}'))
        db.executemany('INSERT INTO entries VALUES(?,?,?,?)',[(batch,k,core.digest(e),core.canonical(e)) for k,e in entries.items()])
        # Keep mapped offers only for unchanged listings. Old prices must never
        # override the replacement workbook or gain an unreviewed branch scope.
        offers=db.execute('SELECT * FROM offers WHERE batch_id=?',(active,)).fetchall()
        kept=[] if replace_existing else [o for o in offers if o['entry_key'] not in changed]
        db.executemany('INSERT INTO offers VALUES(?,?,?,?,?,?,?)',[(batch,o['id'],o['entry_key'],o['amount_cents'],o['max_cents'],o['price_type'],o['payload']) for o in kept])
        report['old_mapped_offers_withheld']=len(offers)-len(kept)
        import spatial
        spatial.index_batch(db,batch)
        report['batch']=batch
        db.execute("UPDATE batches SET status='published',report=? WHERE id=?",(core.canonical(report),batch))
        db.execute("UPDATE settings SET value=? WHERE key='active_batch'",(batch,))
        db.execute('INSERT INTO publish_events(batch_id,previous_batch,created,action) VALUES(?,?,?,?)',(batch,active,time.time(),'publish'))
        return report
