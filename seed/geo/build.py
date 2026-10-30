"""Offline build: python seed/geo/build.py <directory containing downloaded sources>."""
import csv, hashlib, io, json, sys, zipfile
from pathlib import Path
src=Path(sys.argv[1]); out=Path(__file__).parent

def rows(name):
    with zipfile.ZipFile(src/name) as z:
        return list(csv.DictReader(io.StringIO(z.read(z.namelist()[0]).decode('utf-8-sig')), delimiter='|'))

def write(name, data):
    with (out/name).open('w', newline='', encoding='utf-8') as f:
        w=csv.writer(f);w.writerow(['name','state','zip','latitude','longitude']);w.writerows(data)

states=rows('states.zip'); fips={r['GEOID']:r['USPS'] for r in states}
fips.update({'60':'AS','66':'GU','69':'MP','78':'VI'})
write('states.csv',[(r['NAME'],r['USPS'],'',r['INTPTLAT'],r['INTPTLONG']) for r in states])
write('places.csv',[(r['NAME'],r['USPS'],'',r['INTPTLAT'],r['INTPTLONG']) for r in rows('places.zip')])
zs={}
with (src/'relationships.txt').open(encoding='utf-8-sig') as f:
    for r in csv.DictReader(f,delimiter='|'):
        if r['GEOID_ZCTA5_20'] and r['GEOID_COUNTY_20'] and r['GEOID_COUNTY_20'][:2] in fips and int(r['AREALAND_PART'] or 0)>0:
            zs.setdefault(r['GEOID_ZCTA5_20'],set()).add(fips[r['GEOID_COUNTY_20'][:2]])
zdata=[]
for r in rows('zcta.zip'):
    for state in sorted(zs.get(r['GEOID'],set())):
        zdata.append(('',state,r['GEOID'],r['INTPTLAT'],r['INTPTLONG']))
    if r['GEOID'] not in zs: raise ValueError('Missing state relationship: '+r['GEOID'])
write('zcta.csv',zdata)
(out/'checksums.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [src/x for x in ('places.zip','states.zip','zcta.zip','relationships.txt')]+list(out.glob('*.csv'))},indent=2)+'\n')

