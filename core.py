"""Local POC domain and persistence. No external AI, clinical or payment integrations."""
from __future__ import annotations
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB = os.environ.get('DATABASE_URL') or ROOT / 'data' / 'myselfpay.sqlite3'
def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

class SQLiteConnection(sqlite3.Connection):
    """Match the Postgres adapter: commit/rollback and release on context exit."""
    def __exit__(self, kind, value, traceback):
        try:
            return super().__exit__(kind, value, traceback)
        finally:
            self.close()


def connect(path=DB):
    if str(path).startswith(('postgres://','postgresql://')):
        from postgres_store import PostgresConnection
        return PostgresConnection(str(path))
    con = sqlite3.connect(path, timeout=10, factory=SQLiteConnection)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con

def initialize(path=DB):
    if not str(path).startswith(('postgres://','postgresql://')):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, content_hash TEXT UNIQUE NOT NULL,
            source_name TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL, report TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS entries(batch_id TEXT REFERENCES batches(id), entry_key TEXT,
            content_hash TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(batch_id,entry_key));
        CREATE TABLE IF NOT EXISTS offers(batch_id TEXT, id TEXT, entry_key TEXT, amount_cents INTEGER,
            max_cents INTEGER, price_type TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(batch_id,id),
            FOREIGN KEY(batch_id,entry_key) REFERENCES entries(batch_id,entry_key));
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS publish_events(id INTEGER PRIMARY KEY, batch_id TEXT REFERENCES batches(id),
            previous_batch TEXT, created REAL NOT NULL, action TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS members(id TEXT PRIMARY KEY, tenant TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, member_id TEXT REFERENCES members(id),
            csrf TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, tenant TEXT NOT NULL,
            member_id TEXT REFERENCES members(id), title TEXT NOT NULL, payload TEXT NOT NULL,
            updated REAL NOT NULL, expires REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS conversation_owner ON conversations(tenant,member_id);
        CREATE TABLE IF NOT EXISTS login_limits(key TEXT NOT NULL, window_id BIGINT NOT NULL, attempts INTEGER NOT NULL, PRIMARY KEY(key,window_id));
        CREATE TABLE IF NOT EXISTS guest_usage(session_hash TEXT PRIMARY KEY REFERENCES sessions(token_hash), member_id TEXT NOT NULL REFERENCES members(id), payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS preferences(member_id TEXT PRIMARY KEY REFERENCES members(id),
            tenant TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS corrections(id TEXT PRIMARY KEY, tenant TEXT NOT NULL,
            member_id TEXT REFERENCES members(id), offer_id TEXT NOT NULL, batch_id TEXT NOT NULL,
            category TEXT NOT NULL, description TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL);
        ''')
        db.execute('PRAGMA secure_delete=ON')
        import spatial
        spatial.initialize(db)
    if not str(path).startswith(('postgres://','postgresql://')):
        import accounts
        accounts.initialize(path)
    try: os.chmod(path,0o600)
    except OSError: pass

def active_batch(db):
    row=db.execute("SELECT value FROM settings WHERE key='active_batch'").fetchone()
    return row['value'] if row else None

def read_source(path):
    path=Path(path)
    if path.suffix.lower()=='.json': return json.loads(path.read_text(encoding='utf-8'))
    if path.suffix.lower()!='.xlsx': raise ValueError('Use .json or .xlsx')
    try: import openpyxl
    except ImportError as exc: raise ValueError('XLSX import requires openpyxl; the bundled JSON seed needs no extra package.') from exc
    wb=openpyxl.load_workbook(path,read_only=True,data_only=True)
    if 'Directory' not in wb.sheetnames: raise ValueError('Workbook needs a Directory tab')
    keys=['name','area','states','entity','category','mri','ct','other','coverage','notes','source','additional','checked']
    rows=[];started=False
    for number,row in enumerate(wb['Directory'].iter_rows(values_only=True),start=1):
        cells=list(row[:13])+[None]*max(0,13-len(row))
        if str(cells[0]).strip()=='Center or operator': started=True;continue
        if not started or not cells[0]: continue
        vals=[v.strftime('%Y-%m-%d %H:%M:%S') if isinstance(v,(dt.datetime,dt.date)) else '' if v is None else str(v) for v in cells]
        rows.append({'row':str(number-2),**dict(zip(keys,vals))})
    wb.close()
    if not rows: raise ValueError('No directory entries found')
    return rows

def stage_import(source, mappings, path=DB):
    entries=read_source(source); maps=json.loads(Path(mappings).read_text(encoding='utf-8'))
    if not isinstance(entries,list) or not entries: raise ValueError('Source must contain an entry list')
    from facility_locations import enrich, validate_locations
    errors=[]; names=set(); normalized=[]; unplaced=[]; enriched=[]; source_hashes={}
    for entry in entries:
        if not isinstance(entry,dict): raise ValueError('Malformed source entry')
        name=entry.get('name','').strip()
        if not name or name in names: errors.append(f'Missing or duplicate name: {name}')
        names.add(name)
        if urlparse(entry.get('source','')).scheme!='https': errors.append(f'{name}: invalid source URL')
        try: dt.date.fromisoformat(entry.get('checked','')[:10])
        except ValueError: errors.append(f'{name}: missing or invalid verification date')
        source_hashes[name]=digest({k:v for k,v in entry.items() if k!='row'})
        entry, issues=enrich(entry)
        validate_locations(entry)
        enriched.append(entry)
        if issues:unplaced.append({'name':name,'issues':issues,'placed_count':len(entry['locations'])})
        normalized.append((digest(name),digest({k:v for k,v in entry.items() if k!='row'}),entry))
    if errors: raise ValueError('; '.join(errors))
    entries=enriched
    signature=digest({'entries':entries,'mappings':maps})
    with connect(path) as db:
        existing=db.execute('SELECT id,report,status FROM batches WHERE content_hash=?',(signature,)).fetchone()
        if existing: return {'id':existing['id'],'unchanged':True,'status':existing['status'],**json.loads(existing['report'])}
        prior=active_batch(db)
        old={r['entry_key']:r['content_hash'] for r in db.execute('SELECT entry_key,content_hash FROM entries WHERE batch_id=?',(prior,))}
        current={k:h for k,h,e in normalized}
        report={'entry_count':len(entries),'added':sum(k not in old for k in current),
                'changed':sum(k in old and old[k]!=h for k,h in current.items()),
                'removed':sum(k not in current for k in old),'withheld_offers':[],'offer_count':0,
                'unplaced_entries':unplaced,'location_count':sum(len(e['locations']) for e in entries),
                'program_count':sum(e['location_type']=='program' for e in entries)}
        batch=str(uuid.uuid4()); offers=[]; mapping_ids=set()
        lookup={e['name']:(k,h,e) for k,h,e in normalized}
        for m in maps:
            if m.get('id') in mapping_ids: raise ValueError('Duplicate offer mapping id')
            mapping_ids.add(m.get('id'))
            match=lookup.get(m.get('entry_name'))
            if not match or m.get('entry_hash') not in (match[1],source_hashes.get(m.get('entry_name'))):
                report['withheld_offers'].append({'id':m.get('id'),'reason':'Source entry changed or missing; mapping needs human review'})
                continue
            if m.get('type') not in ('fixed','starting','range','maximum','conflict','average','quote'): raise ValueError('Invalid price type')
            if m.get('contrast') not in ('unknown','without','with','both'): raise ValueError('Invalid contrast')
            if m.get('mod') not in ('MRI','CT'):raise ValueError('Unsupported mapped modality')
            if m['type'] in ('fixed','starting','range','maximum','average') and m.get('amount') is None:raise ValueError('Numeric price type needs an amount')
            for field in ('amount','max'):
                if field in m:
                    from decimal import Decimal
                    value=m[field]
                    if isinstance(value,bool) or not isinstance(value,(int,float)):raise ValueError('Invalid money')
                    decimal=Decimal(str(value))
                    if not decimal.is_finite() or decimal<0 or decimal*100!=(decimal*100).to_integral_value():raise ValueError('Money must be nonnegative with at most two decimals')
            if m['type']=='range' and (m.get('max') is None or m['max']<m['amount']):raise ValueError('Invalid price range')
            offers.append((m,match[0]))
        report['offer_count']=len(offers)
        db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)',(batch,signature,Path(source).name,time.time(),'staged',canonical(report)))
        db.executemany('INSERT INTO entries VALUES(?,?,?,?)',[(batch,k,h,canonical(e)) for k,h,e in normalized])
        import spatial
        spatial.index_batch(db, batch)
        for m,key in offers:
            from decimal import Decimal
            cents=lambda v: int(Decimal(str(v))*100) if v is not None else None
            db.execute('INSERT INTO offers VALUES(?,?,?,?,?,?,?)',(batch,m['id'],key,cents(m.get('amount')),cents(m.get('max')),m['type'],canonical(m)))
    return {'id':batch,'unchanged':False,'status':'staged',**report}

def publish(batch,path=DB):
    with connect(path) as db:
        row=db.execute('SELECT status FROM batches WHERE id=?',(batch,)).fetchone()
        if not row: raise ValueError('Unknown batch')
        old=active_batch(db)
        if old==batch: return {'active_batch':batch,'unchanged':True}
        db.execute("INSERT INTO settings VALUES('active_batch',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(batch,))
        db.execute("UPDATE batches SET status='published' WHERE id=?",(batch,))
        db.execute('INSERT INTO publish_events(batch_id,previous_batch,created,action) VALUES(?,?,?,?)',(batch,old,time.time(),'publish' if row['status']=='staged' else 'rollback'))
    return {'active_batch':batch,'previous_batch':old}

def directory_summary(path=DB):
    """Public counts only; no sessions or personal records are read."""
    import re
    valid=set('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split())
    with connect(path) as db:
        rows=db.execute("SELECT payload FROM entries WHERE batch_id=(SELECT value FROM settings WHERE key='active_batch')").fetchall()
    states=set()
    for row in rows:
        entry=json.loads(row['payload'])
        states.update(s.strip().upper() for s in re.split(r'[;,/|]+',entry.get('states','')))
        states.update(str(loc.get('state','')).strip().upper() for loc in entry.get('locations',[]))
    return {'listing_count':len(rows),'state_count':len(states & valid)}

def catalog(path=DB):
    with connect(path) as db:
        batch=active_batch(db)
        entries={r['entry_key']:{**json.loads(r['payload']), 'booking_id': r['entry_key']} for r in db.execute('SELECT * FROM entries WHERE batch_id=?',(batch,))}
        offers=[]
        for r in db.execute('SELECT * FROM offers WHERE batch_id=?',(batch,)):
            o=json.loads(r['payload']);o['entry']=entries[r['entry_key']];o['version']=batch
            o['amount']=r['amount_cents']/100 if r['amount_cents'] is not None else None
            if r['max_cents'] is not None:o['max']=r['max_cents']/100
            offers.append(o)
    return {'version':batch,'entries':list(entries.values()),'offers':offers,'locations':[label for label,city in location_choices(entries.values())]}

LOCATION_CHOICES = [('Cincinnati, OH','Cincinnati'),('Louisville, KY','Louisville'),('Lexington, KY','Lexington'),('Philadelphia, PA','Philadelphia'),('Boise, ID','Boise'),('Lawrenceville, GA','Lawrenceville'),('Auburn, WA','Auburn'),('Spanish Fork, UT','Spanish Fork'),('South Burlington, VT','South Burlington'),('Wisconsin','Wisconsin'),('National','National')]

def location_choices(entries):
    # Enriched records have already resolved geography during import.
    from geo import lookup, state_choices
    choices = list(LOCATION_CHOICES)
    choices.extend((name, name) for name in state_choices())
    for entry in entries:
        for loc in entry.get('locations', []):
            city = str(loc.get('city', '')).strip()
            state = str(loc.get('state', '')).strip()
            if city and state:
                choices.append((f'{city}, {state}', city))
        # Preserve compatibility for legacy rows that predate import enrichment.
        # Explicitly unresolved/program records must not repeat fuzzy lookup.
        if 'location_type' not in entry:
            state = str(entry.get('states', '')).strip()
            if len(state) == 2:
                for city in str(entry.get('area', '')).split('/'):
                    city = city.strip()
                    if city:
                        for candidate in lookup(f'{city}, {state}'):
                            if candidate['match'] == 'exact':
                                choices.append((candidate['label'], candidate['name']))
    return list(dict.fromkeys(choices))


def location_options(value, choices=None):
    from difflib import SequenceMatcher
    choices = LOCATION_CHOICES if choices is None else choices
    q=value.strip().casefold()
    exact=[label for label,city in choices if q in (label.casefold(),city.casefold())]
    if exact:return exact
    return [label for label,city in choices if q and (city.casefold().startswith(q) or SequenceMatcher(None,q,city.casefold()).ratio()>=0.65)]

def clean_search(raw):
    mod=raw.get('mod',''); contrast=raw.get('contrast','unknown'); typ=raw.get('type','all')
    if mod not in ('','MRI','CT') or contrast not in ('unknown','without','with','both'): raise ValueError('Invalid procedure or contrast')
    if typ not in ('all','fixed','starting','range','maximum','conflict','average','quote'): raise ValueError('Invalid price filter')
    try: budget=float(raw.get('budget') or 0)
    except (TypeError,ValueError): raise ValueError('Invalid budget')
    if not 0<=budget<=1000000: raise ValueError('Invalid budget')
    radius=raw.get('radius',50)
    if isinstance(radius,bool) or radius not in (25,50,100,250):raise ValueError('Choose a radius of 25, 50, 100 or 250 miles')
    return {'scope':'state' if raw.get('scope')=='state' else 'radius','radius':radius,'mod':mod,'contrast':contrast,'type':typ,'body':str(raw.get('body',''))[:100],
            'location':str(raw.get('location',''))[:120].strip(),'budget':budget,
            'sort':raw.get('sort') if raw.get('sort') in ('price','scope') else 'distance',
            'selected':list(dict.fromkeys(x for x in raw.get('selected',[]) if isinstance(x,str)))[:3]}

def search(raw,path=DB):
    s=clean_search(raw); data=catalog(path); result=[]; location=s['location'].casefold()
    from geo import lookup
    from proximity import nearest, offer_locations, annotate, covers
    candidates=lookup(s['location']) if location != 'national' else []
    # Rank ambiguous exact cities by existing facility coverage; never auto-correct typos.
    if len(candidates)>1 and all(c['match']=='exact' and c['kind']=='place' for c in candidates):
        locations=[loc for entry in data['entries'] if entry.get('location_type')!='program' for loc in entry.get('locations',[])]
        def distance(candidate):
            match=nearest(candidate,locations)
            return match[0] if match else float('inf')
        candidates=sorted(candidates,key=lambda c:(distance(c),c['label']))
        nearby_candidates=[c for c in candidates if distance(c)<=s['radius']]
        if len(nearby_candidates)==1:candidates=nearby_candidates
        elif len(nearby_candidates)>1:
            # Rank covered same-name cities by Census population, not proximity
            # to a facility. Keep the state choice explicit for ambiguous names.
            nearby_candidates.sort(key=lambda c:(-(c.get('population') if c.get('population') is not None else -1),c['label']))
            candidates=nearby_candidates+[c for c in candidates if c not in nearby_candidates]
    suggestions=list(dict.fromkeys(c['label'] for c in candidates))
    needs_choice=bool(candidates) and (len(suggestions)>1 or candidates[0]['match']!='exact')
    selected_state=None
    notice=''
    resolved=None
    if location=='national':
        pass
    elif needs_choice:
        location='__unconfirmed_location__'
        notice='Choose a location below. No location has been selected automatically.'
    elif candidates:
        resolved=candidates[0]
        notice=resolved.get('notice', '')
        selected_state=resolved['state']
        s['location']=resolved['label']
        if resolved['kind']=='zcta':
            location=selected_state.casefold()
            notice='Distances are approximate straight-line miles from the Census ZIP-area reference point, not driving distances.'
        elif resolved['kind']=='state':location=selected_state.casefold()
        else:location=resolved['name'].casefold()
    elif location:
        location='__unknown_location__'
        notice='We could not find that place. Please enter a five-digit ZIP code. Not every postal ZIP has a Census ZIP area.'
    statewide=bool(resolved and s['scope']=='state')
    spatial=bool(resolved and resolved['kind'] in ('place','zcta') and not statewide)
    def in_state(entry):
        return covers(entry,selected_state) or any(loc.get('state')==selected_state for loc in entry.get('locations',[]))
    def state_distance(item, locations):
        match=nearest(resolved,[loc for loc in locations if loc.get('state')==selected_state]) if resolved['kind']!='state' else None
        return annotate(item,match) if match else dict(item)
    if statewide:
        location=selected_state.casefold()
        state_name=next((c['name'] for c in lookup(selected_state) if c['kind']=='state'),selected_state)
        notice=f'Showing indexed imaging centers across {state_name}. Exam and price filters still apply. '
        if resolved['kind']!='state':notice+='Known distances are approximate straight-line miles from your searched location; centers without verified coordinates are also included.'
    programs=[]; unlocated=[]
    def same_exam(o):
        if s['mod'] and o['mod']!=s['mod']:return False
        if s['contrast']!='unknown' and o['contrast']!=s['contrast']:return False
        terms=o.get('exam_terms')
        return not s['body'].strip() or not terms or any(__import__('re').search(r'(?<!\w)'+__import__('re').escape(term.casefold())+r'(?!\w)',s['body'].casefold()) for term in terms)
    if spatial and not notice:notice='Distances are approximate straight-line miles from the searched city center, not driving distances.'
    for original in data['offers']:
        o=dict(original)
        if not location or (not statewide and (not s['mod'] or not s['body'].strip())):continue
        e=o['entry']
        if not spatial and selected_state and not in_state(e):continue
        if not same_exam(o) or (s['type']!='all' and s['type']!=o['type']):continue
        if not spatial and not statewide and not (location=='national' or location in o['region'].casefold() or location in e['area'].casefold() or location in [x.strip().casefold() for x in e['states'].split(';')]):continue
        o['match_status']='Exact body-part and protocol applicability requires confirmation'
        o['budget_status']='Budget fit unconfirmed'
        if not s['budget']:o['budget_status']='No budget set'
        elif o['type']=='fixed' and o['amount']>s['budget']:o['budget_status']='Above your published-price budget'
        elif o['type']=='fixed' and o['contrast']!='unknown':o['budget_status']='Published amount within budget · exact scope needs confirmation'
        if spatial:
            if e.get('location_type')=='program':
                if covers(e,selected_state):programs.append(o)
                continue
            match=nearest(resolved,offer_locations(o))
            if not match:unlocated.append(o['id']);continue
            o=annotate(o,match)
        if statewide:
            if e.get('location_type')=='program':
                programs.append(o)
                continue
            # Do not show a mapped price known to apply only outside this state.
            price_locations=offer_locations(o)
            if price_locations and not any(loc.get('state')==selected_state for loc in price_locations):continue
            o=state_distance(o,price_locations)
        result.append(o)
    fallback=False; nearby=[]
    if spatial:
        s['sort']='distance'
        result.sort(key=lambda o:o['_distance'])
        within=[o for o in result if o['_distance']<=s['radius']]
        fallback=bool(result and not within)
        result=within if within else result[:3]
        represented_names={o['entry']['name'] for o in result+programs}
        priced_names={o['entry']['name'] for o in data['offers'] if o.get('mod')==s['mod'] and o.get('amount') is not None}
        for entry in data['entries']:
            if not s['mod'] or not s['body'].strip() or entry.get('location_type')=='program' or entry['name'] in priced_names or entry['name'] in represented_names:continue
            wording=str(entry.get(s['mod'].lower(),'')).strip()
            if not wording or wording.casefold() in ('n/a','none','no'):continue
            if __import__('re').search(r'\b(?:not (?:offered|listed|verified|available)|unavailable)\b',wording,__import__('re').I):continue
            # Entries with published price wording but unmapped offers are not quote-only.
            if '$' in wording or any(c.isdigit() for c in wording):continue
            match=nearest(resolved,entry.get('locations',[]))
            if match:nearby.append(annotate(entry,match))
        nearby.sort(key=lambda e:e['_distance'])
        nearby_within=[e for e in nearby if e['_distance']<=s['radius']]
        if nearby_within:nearby=nearby_within
        elif not result and not programs:
            nearby=nearby[:3]
            if nearby:
                notice+=f" No matching offers or quote facilities within {s['radius']} miles. Showing the nearest quote facilities outside your radius; confirm the exact exam and contrast."
        else:nearby=[]
        if unlocated:notice+=' Some offers lack verified price-location coordinates and are omitted from distance results; browse National to review them.'
    # Relax commercial filters only; preserve the ordered procedure.
    alternative_heading=''
    if spatial and not result and s['mod'] and s['body'].strip():
        alternatives=[]
        for original in data['offers']:
            if original['entry'].get('location_type')=='program' or not same_exam(original):continue
            match=nearest(resolved,offer_locations(original))
            if not match:continue
            o=annotate(dict(original),match)
            differences=[]
            if s['type']!='all' and s['type']!=o['type']:differences.append('Different price type: '+o['type'])
            if o['_distance']>s['radius']:differences.append('Outside selected radius')
            o['budget_status']='No budget set' if not s['budget'] else 'Budget fit unconfirmed'
            if s['budget'] and o.get('amount') is not None and o['amount']>s['budget']:
                differences.append('Above your budget');o['budget_status']='Above your published-price budget'
            o['filter_differences']=differences
            o['reviewed_alternative']=True
            o['match_status']='Reviewed alternative — does not match all selected filters. Confirm your ordered exam with the facility; this is not a substitute test.'
            alternatives.append((o['_distance'],str(o['id']),o))
        if alternatives:
            alternatives.sort(key=lambda item:item[:2])
            result=[alternatives[0][2]]
            fallback=True
            alternative_heading='Nearest reviewed alternative'
            nearby=[e for e in nearby if e['name']!=result[0]['entry']['name']]
            notice+=' No reviewed facility offer matches all selected filters. Showing the nearest reviewed alternative for the selected procedure. Differences are marked on the card; your filters are unchanged. This is not a substitute for your ordered exam.'
    no_exam_price=bool(s['mod'] and s['body'].strip() and (spatial or location=='national' or selected_state) and not any(same_exam(o) and o.get('amount') is not None for o in data['offers']))
    if no_exam_price:
        notice+=' No listed facility publishes a price for this exam.'
        if spatial:
            quote_leads=[]
            for entry in data['entries']:
                if entry.get('location_type')=='program':continue
                wording=str(entry.get(s['mod'].lower(),'')).strip()
                if not wording or wording.casefold() in ('n/a','none','no'):continue
                if __import__('re').search(r'\b(?:not (?:offered|listed|verified|available)|unavailable)\b',wording,__import__('re').I):continue
                match=nearest(resolved,entry.get('locations',[]))
                if match:quote_leads.append(annotate(entry,match))
            nearby=sorted(quote_leads,key=lambda e:e['_distance'])[:3]
    if statewide:
        represented={o['entry']['name'] for o in result}
        for entry in data['entries']:
            if entry.get('location_type')=='program' or not in_state(entry) or entry['name'] in represented:continue
            wording=str(entry.get(s['mod'].lower(),'')).strip() if s['mod'] else 'Service availability requires confirmation'
            if not wording or wording.casefold() in ('n/a','none','no'):continue
            if __import__('re').search(r'\b(?:not (?:offered|listed|verified|available)|unavailable)\b',wording,__import__('re').I):continue
            nearby.append(state_distance(entry,entry.get('locations',[])))
        result.sort(key=lambda o:(o.get('_distance',float('inf')),o['entry']['name']))
        nearby.sort(key=lambda e:(e.get('_distance',float('inf')),e['name']))
        alternative_heading=f'Imaging centers across {state_name}'
    if not spatial and s['sort']=='price':result.sort(key=lambda o:o['amount'] if o['type']=='fixed' and o['amount'] is not None else float('inf'))
    selected=set(o['id'] for o in result+programs);s['selected']=[x for x in s['selected'] if x in selected]
    return {'version':data['version'],'search':s,'offers':result,'count':len(result),'location_suggestions':suggestions if needs_choice else [],'location_notice':notice,'resolved_location':resolved,'programs':programs,'nearby_unpriced':nearby,
            'no_exam_price':no_exam_price,'fallback':fallback,'results_heading':alternative_heading or (f"Nothing within {s['radius']} miles. Nearest options:" if fallback else ''),
            'program_heading':'Programs available in '+next((c['name'] for c in lookup(selected_state or '') if c['kind']=='state'),selected_state or ''),
            'unlocated_offer_count':len(unlocated),
            'program_entries':[e for e in data['entries'] if (statewide or (spatial and s['mod'] and s['body'].strip())) and e.get('location_type')=='program' and covers(e,selected_state) and e['name'] not in {o['entry']['name'] for o in programs}]}

def agent(message,raw,path=DB):
    import re
    if not isinstance(message,str) or not message.strip() or len(message)>2000:raise ValueError('Use a message of 1–2000 characters')
    s=clean_search(raw);low=message.casefold()
    if re.search(r'what.*(test|scan).*need|diagnos|symptom|should i get',low):
        reply='I can help find published prices, but cannot diagnose or decide which test you need. Confirm the exam with your clinician.'
    elif 'second opinion' in low:
        reply='Do you mean a specialist consultation, a review of existing images, or a new exam? Consultation and image-review providers need separate research.'
    else:
        if re.search(r'\bct\b',low):s['mod']='CT'
        if re.search(r'\bmri\b',low):s['mod']='MRI'
        for body in ('knee','brain','lumbar','shoulder'):
            if body in low:s['body']=body.title()
        if 'without and with' in low or 'with and without' in low:s['contrast']='both'
        elif 'without contrast' in low or 'noncontrast' in low:s['contrast']='without'
        elif 'with contrast' in low:s['contrast']='with'
        money=re.search(r'(?:under|below|budget)\s*\$?(\d+)',low)
        if money:s['budget']=float(money[1])
        place=re.search(r'\b(?:near|in)\s+(.+?)(?=\s+(?:under|below|budget|with|without)\b|[.!?]|$)',message,re.I)
        if place:s['location']=place.group(1).strip()
        elif any(c['match']=='exact' for c in __import__('geo').lookup(message.strip())) or message.strip().casefold()=='national':s['location']=message.strip()
        reply='Is your ordered exam without contrast, with contrast, or without and with contrast? If unsure, confirm with your clinician.' if s['contrast']=='unknown' else 'Your search has been updated. Review the sourced candidates below and confirm the exact study, eligibility and total with the facility. These are published references, not current quotes.'
    guidance_blocked=bool(re.search(r'what.*(test|scan).*need|diagnos|symptom|should i get',low)) or 'second opinion' in low
    if not guidance_blocked and not s['mod']:reply='What imaging service are you looking for: MRI or CT? Use the exam specified by your clinician.'
    elif not guidance_blocked and not s['body'].strip():reply='What body part is the exam for? Use the wording on your order, if you have one.'
    elif not guidance_blocked and not s['location']:reply='What city and state or five-digit ZIP would you like to search? Distances are approximate straight-line miles.'
    result=search(s,path)
    if not guidance_blocked and result['location_suggestions']:
        reply='Did you mean '+ ' or '.join(result['location_suggestions'])+'? Select a location below to update the search.'
    elif not guidance_blocked and result['offers'] and any(o['contrast']=='unknown' for o in result['offers']):
        reply+=' Some listings have unconfirmed contrast or protocol. A published amount below your budget does not confirm that it covers your requested exam or all fees; ask the facility for the exact total.'
    if not guidance_blocked and result.get('location_notice'):reply+=' '+result['location_notice']
    return {'reply':reply,**result,'agent_mode':'deterministic-demo'}

class GuestCreationLimited(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__('Guest sign-in is temporarily at capacity. Please try again later.')


def reserve_guest_creation(db):
    """Serialize admission in the same transaction as account creation.

    One persistent settings row keeps counters bounded and shared across workers.
    The write locks that row on PostgreSQL and the writer on SQLite.
    Failed creation rolls the reservation back; rejected requests add no rows.
    """
    hour_limit = int(os.environ.get('GUEST_GLOBAL_PER_HOUR', '200'))
    day_limit = int(os.environ.get('GUEST_GLOBAL_PER_DAY', '2000'))
    lock_usage(db)
    consume_windows(db, [('guest-create-hour',3600,hour_limit),('guest-create-day',86400,day_limit)])


def guest_login(path=DB):
    """A separate identity per guest session; never reuse a shared demo member."""
    ident=str(uuid.uuid4());token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(24)
    with connect(path) as db:
        reserve_guest_creation(db)
        db.execute('INSERT INTO members VALUES(?,?,?,?,?,?)',
            (ident,'guest',ident+'@guest.invalid','Guest',secrets.token_hex(16),secrets.token_hex(32)))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?)',
            (hashlib.sha256(token.encode()).hexdigest(),ident,csrf,time.time()+8*3600))
    return {'token':token,'csrf':csrf,'member':{'id':ident,'tenant':'guest','name':'Guest'}}

def session(token,path=DB):
    with connect(path) as db:
        row=db.execute('SELECT m.id,m.tenant,m.name,m.email,s.csrf FROM sessions s JOIN members m ON m.id=s.member_id WHERE s.token_hash=? AND s.expires>?',(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
        if row and db.execute("SELECT value FROM settings WHERE key=? AND value='1'", ('account-disabled:' + row['id'],)).fetchone():
            return None
        return dict(row) if row else None

def history(member,path=DB):
    if member['tenant']=='guest':return []
    with connect(path) as db:
        db.execute('PRAGMA secure_delete=ON')
        import spatial
        spatial.initialize(db)
        return [{'id':r['id'],'title':r['title'],'updated':r['updated']*1000,'expires':r['expires']*1000,**json.loads(r['payload'])} for r in db.execute('SELECT * FROM conversations WHERE tenant=? AND member_id=? AND expires>? ORDER BY updated DESC',(member['tenant'],member['id'],time.time()))]

def save_conversation(member,data,path=DB):
    if member['tenant']=='guest':raise PermissionError('Guest conversations cannot be saved')
    if data.get('private'):raise ValueError('Private conversations cannot be persisted')
    ident=data.get('id') or str(uuid.uuid4());messages=data.get('messages',[])
    if not isinstance(messages,list) or not 0<=len(messages)<=200:raise ValueError('History must contain at most 200 messages')
    if not messages and not data.get('search',{}).get('location'):raise ValueError('A saved search needs a location')
    for m in messages:
        if not isinstance(m,dict) or m.get('role') not in ('user','assistant') or not isinstance(m.get('text'),str) or len(m['text'])>4000:raise ValueError('Invalid message')
    result=search(data.get('search',{}),path); now=time.time()
    payload=canonical({'messages':messages,'search':result['search'],'version':result['version']})
    with connect(path) as db:
        row=db.execute('SELECT tenant,member_id FROM conversations WHERE id=?',(ident,)).fetchone()
        if row and (row['tenant']!=member['tenant'] or row['member_id']!=member['id']):raise PermissionError('Conversation unavailable')
        changed=db.execute('INSERT INTO conversations VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,payload=excluded.payload,updated=excluded.updated,expires=excluded.expires WHERE conversations.tenant=excluded.tenant AND conversations.member_id=excluded.member_id',
            (ident,member['tenant'],member['id'],f"{result['search']['mod']} · {result['search']['location']}",payload,now,now+90*86400)).rowcount
        if not changed:raise PermissionError('Conversation unavailable')
    return {'id':ident}

def delete_conversation(member,ident,path=DB):
    with connect(path) as db:
        db.execute('PRAGMA secure_delete=ON')
        import spatial
        spatial.initialize(db)
        if ident=='all':db.execute('DELETE FROM conversations WHERE tenant=? AND member_id=?',(member['tenant'],member['id']))
        else:
            count=db.execute('DELETE FROM conversations WHERE id=? AND tenant=? AND member_id=?',(ident,member['tenant'],member['id'])).rowcount
            if not count:raise PermissionError('Conversation unavailable')

def preference(member,value=None,delete=False,path=DB):
    if member['tenant']=='guest' and not delete:
        if value is not None:raise PermissionError('Guest locations are not saved')
        return {}
    with connect(path) as db:
        if delete:db.execute('DELETE FROM preferences WHERE member_id=? AND tenant=?',(member['id'],member['tenant']))
        elif value is not None:
            location=str(value.get('location',''))[:120]
            db.execute('INSERT INTO preferences VALUES(?,?,?) ON CONFLICT(member_id) DO UPDATE SET payload=excluded.payload',(member['id'],member['tenant'],canonical({'location':location})))
        row=db.execute('SELECT payload FROM preferences WHERE member_id=? AND tenant=?',(member['id'],member['tenant'])).fetchone()
        return json.loads(row['payload']) if row and member['tenant']!='guest' else {}


GUEST_LIMIT_MESSAGE = 'Guest preview limit reached. Full access is coming with MySelfPay accounts.'

class GuestAgentLimited(Exception):
    def __init__(self, retry_after=0, reason="session"):
        self.retry_after=retry_after
        self.reason=reason
        super().__init__(GUEST_LIMIT_MESSAGE)

def lock_usage(db):
    # Persistent singleton write lock: admission and counters commit together.
    db.execute("INSERT INTO settings(key,value) VALUES('guest_usage_lock','1') ON CONFLICT(key) DO NOTHING")
    db.execute("UPDATE settings SET value=value WHERE key='guest_usage_lock'")

def consume_windows(db, limits, agent=False):
    now=time.time()
    for name,seconds,limit in limits:
        if limit<1:raise RuntimeError('Limits must be positive')
        key=hashlib.sha256(name.encode()).hexdigest()
        start=int(now//seconds)*seconds//60
        row=db.execute('SELECT attempts FROM login_limits WHERE key=? AND window_id=?',(key,start)).fetchone()
        if row and row['attempts']>=limit:
            retry=max(1,int((int(now//seconds)+1)*seconds-now)+1)
            raise GuestAgentLimited(retry, "hourly" if seconds==3600 else "daily") if agent else GuestCreationLimited(retry)
        db.execute('INSERT INTO login_limits VALUES(?,?,1) ON CONFLICT(key,window_id) DO UPDATE SET attempts=login_limits.attempts+1',(key,start))

def guest_usage(token, path=DB):
    key=hashlib.sha256(token.encode()).hexdigest()
    with connect(path) as db:
        row=db.execute('SELECT payload FROM guest_usage WHERE session_hash=?',(key,)).fetchone()
    return json.loads(row['payload']) if row else {}

def guest_agent(token, ip, message, raw, conversation_id, limits, path=DB):
    conversations,messages,characters,per_ip,global_day=limits
    if not isinstance(message,str) or not message.strip():raise ValueError('Enter a message')
    if len(message)>characters:raise GuestAgentLimited(reason="characters")
    if conversation_id is not None and not isinstance(conversation_id,str):raise ValueError('Invalid conversation')
    # Validate inexpensive input before reserving quota; never generate before
    # the session and all shared limits have been checked atomically.
    clean_search(raw)
    key=hashlib.sha256(token.encode()).hexdigest()
    with connect(path) as db:
        lock_usage(db)
        member=db.execute("SELECT s.member_id FROM sessions s JOIN members m ON m.id=s.member_id WHERE s.token_hash=? AND s.expires>? AND m.tenant='guest'",(key,time.time())).fetchone()
        if not member:raise PermissionError('Session expired')
        row=db.execute('SELECT payload FROM guest_usage WHERE session_hash=?',(key,)).fetchone()
        usage=json.loads(row['payload']) if row else {}
        if conversation_id is None:
            if len(usage)>=conversations:raise GuestAgentLimited()
            conversation_id=str(uuid.uuid4())
        elif conversation_id not in usage:raise ValueError('Conversation unavailable in this guest session')
        if usage.get(conversation_id,0)>=messages:raise GuestAgentLimited(reason="conversation")
        consume_windows(db,[('guest-agent-ip:'+ip,3600,per_ip),('guest-agent-global',86400,global_day)],agent=True)
        usage[conversation_id]=usage.get(conversation_id,0)+1
        db.execute('INSERT INTO guest_usage VALUES(?,?,?) ON CONFLICT(session_hash) DO UPDATE SET payload=excluded.payload',(key,member['member_id'],canonical(usage)))
    # Reservation commits before potentially slow/paid generation, outside the
    # lock. Failed generation consumes the attempt (no retry/refund loophole).
    answer=agent(message,raw,path)
    return {**answer,'conversation_id':conversation_id,'usage':usage}

