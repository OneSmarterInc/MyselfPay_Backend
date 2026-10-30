"""Import-time geography only. Does not attach prices to sites."""
import math
import json
import hashlib
from pathlib import Path
import re
from geo import lookup


def validate_locations(entry):
    if not isinstance(entry.get('locations'), list):
        raise ValueError('locations must be a list')
    if entry.get('location_type') not in ('site', 'program', 'unresolved'):
        raise ValueError('Invalid location_type')
    if entry['location_type']=='program' and entry['locations']:
        raise ValueError('Programs cannot have site coordinates')
    if (entry['location_type']=='site') != bool(entry['locations']):
        raise ValueError('Location status does not match locations')
    for loc in entry['locations']:
        if not isinstance(loc,dict):raise ValueError('Invalid location')
        if not isinstance(loc.get('city'),str) or not re.fullmatch(r'[A-Z]{2}',loc.get('state','')):
            raise ValueError('Location needs city and state')
        if loc.get('precision') not in ('address','zip','city'):raise ValueError('Invalid precision')
        if loc.get('precision')=='zip' and not loc.get('zip'):raise ValueError('ZIP precision needs ZIP')
        if loc.get('zip') and not re.fullmatch(r'\d{5}',loc['zip']):raise ValueError('Invalid ZIP')
        for key,limit in [('latitude',90),('longitude',180)]:
            v=loc.get(key)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>limit:
                raise ValueError('Invalid '+key)


def enrich(entry):
    """Return a copy with derived locations plus unresolved source fragments."""
    result=dict(entry);result['locations']=[]
    area=str(entry.get('area','')).strip()
    states=[s.strip().upper() for s in re.split(r'[;,/]',str(entry.get('states',''))) if s.strip()]
    program=entry.get('location_type')=='program' or bool(re.fullmatch(r'national|nationwide|multi-state|multiple states|multiple markets|participating sites|all states|united states|usa',area,re.I))
    if not area and len(states)>1 and re.search(r'program|national|network|marketplace',str(entry.get('entity','')),re.I):program=True
    if program:
        result['location_type']='program';validate_locations(result);return result,[]
    # Structured locations can disambiguate multi-state source rows; recompute
    # their coordinates instead of trusting supplied numeric values.
    # Only a state immediately followed by a ZIP is an address candidate;
    # this avoids phone numbers, prices, and unrelated five-digit numbers.
    addresses=[]
    for fragment in re.split(r'[;\n]', str(entry.get('coverage',''))):
        match=re.search(r'(?:,\s*|\s+)([A-Z]{2})\s+(\d{5})(?:-\d{4})?\b',fragment)
        if match:
            prefix=fragment[:match.start()].strip(' ,')
            city=prefix.rsplit(',',1)[-1].strip() if ',' in prefix else ''
            # A comma-less address does not provide a reliably parsed city.
            if any(c.isdigit() for c in city):city=''
            addresses.append((city,match[1],match[2]))
    supplied=entry.get('locations')
    evidence_path=Path(__file__).resolve().parent/'seed/facility-location-evidence.json'
    if not supplied and evidence_path.exists():
        evidence=json.loads(evidence_path.read_text()).get(entry.get('name'),{})
        fingerprint=hashlib.sha256(json.dumps({k:v for k,v in entry.items() if k!='row'},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        if evidence.get('source_hash')==fingerprint:supplied=evidence['locations']
    if supplied is not None and not isinstance(supplied,list):raise ValueError('locations must be a list')
    if supplied:
        if any(not isinstance(x,dict) for x in supplied):raise ValueError('Invalid location')
        parts=[(str(x.get('city','')),str(x.get('state','')),str(x.get('zip',''))) for x in supplied]
    elif addresses:
        parts=list(dict.fromkeys(addresses))
    else:
        # Preserve city/state pairs while splitting comma and 'and' city lists.
        coverage=str(entry.get('coverage','')).strip()
        if re.search(r'\b(?:area|suburbs)\b',area,re.I) and coverage and not re.search(r'\d|[.:]',coverage):area=coverage
        area=re.sub(r'\s+and\s+', ';', area, flags=re.I)
        area=re.sub(r',(?!\s*[A-Z]{2}(?:\s|$|;))\s*', ';', area)
        parts=[(p.strip(),states[0] if len(states)==1 else '',str(entry.get('zip','')).strip()) for p in re.split(r'[;/]',area) if p.strip()]
        if not parts:parts=[('',states[0] if len(states)==1 else '',str(entry.get('zip','')).strip())]
    unresolved=[]
    for city,state,zip_code in parts:
        label=city
        m=re.search(r'\b(\d{5})(?:-\d{4})?\b',city)
        if m:zip_code=m[1];city=city[:m.start()].strip(' ,')
        m=re.search(r',\s*([A-Za-z]{2})$',city)
        if m:state=m[1].upper();city=city[:m.start()].strip()
        query=(zip_code or city)+((', '+state) if state else '')
        candidates=[c for c in lookup(query) if c['match']=='exact' and c['kind']==('zcta' if zip_code else 'place')]
        if len(candidates)!=1:
            unresolved.append({'input':label or query,'reason':'Unknown or ambiguous ZIP/city; review required'});continue
        c=candidates[0]
        if states and c['state'] not in states:
            unresolved.append({'input':label or query,'reason':'Location conflicts with directory state'});continue
        # ZIP and an explicitly named city must agree on state.
        if zip_code and city:
            cities=[x for x in lookup(city+', '+c['state']) if x['match']=='exact' and x['kind']=='place']
            if len(cities)==1:city=cities[0]['name']
            # Census places omit some postal towns. Coordinates remain ZIP-level,
            # and the city label is source text, never an address-level claim.
        loc={'city':city if zip_code else c['name'],'state':c['state'],'latitude':c['latitude'],'longitude':c['longitude'],'precision':'zip' if zip_code else 'city'}
        if zip_code:loc['zip']=zip_code
        if loc not in result['locations']:result['locations'].append(loc)
    result['location_type']='site' if result['locations'] else 'unresolved'
    validate_locations(result)
    return result,unresolved


