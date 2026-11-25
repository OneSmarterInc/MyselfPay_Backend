"""Delaware MRI/CT source prices and ZIP-reference comparisons."""
import csv,hashlib,math
from pathlib import Path
from functools import lru_cache
from decimal import Decimal
from chr_california import numeric

REQUIRED={'State','Hospital','Place ID','CCN','City','Zip','Modality','Contrast','CPT code','Setting','Billing class','professional','Cash price','Gross charge','Insurer rate min','Insurer rate median','Insurer rate max'}

@lru_cache(maxsize=1)
def zip_references():
    import geo
    with (geo.DATA/'zcta.csv').open(encoding='utf-8',newline='') as f:
        return {r['zip']:(float(r['latitude']),float(r['longitude'])) for r in csv.DictReader(f) if r['state']=='DE'}

def source_rows(path):
    with open(path,encoding='utf-8-sig',newline='') as file:
        reader=csv.DictReader(file)
        if not REQUIRED.issubset(reader.fieldnames or []):raise ValueError('Missing required Delaware source columns.')
        for line,row in enumerate(reader,2):
            if None in row or any(v is None for v in row.values()):raise ValueError(f'Row {line}: inconsistent CSV columns.')
            if row['State']!='DE' or not row['Hospital'].strip() or not row['Place ID'].strip():raise ValueError(f'Row {line}: Delaware hospital identity is required.')
            if row['Modality'] not in ('MRI','CT') or row['Contrast'].lower() not in ('without','with'):raise ValueError(f'Row {line}: invalid modality or contrast.')
            zip5=row['Zip'].strip()
            if len(zip5)!=5 or not zip5.isascii() or not zip5.isdigit():raise ValueError(f'Row {line}: five-digit hospital ZIP required.')
            lat,lng=zip_references().get(zip5,(None,None))
            flag=row['professional'].strip().lower()
            if flag not in ('','0','1','true','false'):raise ValueError(f'Row {line}: invalid professional flag.')
            professional=None if not flag else flag in ('1','true')
            yield (line,'DE',row['Place ID'],row['CCN'],row['Hospital'],row['City'],zip5,row['Modality'],row['Contrast'].lower(),row['CPT code'],row['Setting'],row['Billing class'],professional,*(numeric(row[k],line) for k in ('Cash price','Gross charge','Insurer rate min','Insurer rate median','Insurer rate max')),lat,lng,row)

def validate_source(path):
    count=0;hospitals=set()
    for row in source_rows(path):count+=1;hospitals.add(row[2])
    if not count:raise ValueError('CSV has no price records.')
    return count,len(hospitals),hashlib.sha256(Path(path).read_bytes()).hexdigest()

def origin(query):
    import geo
    zip5=str(query.get('zip','')).strip()
    if zip5:
        if zip5 not in zip_references():raise ValueError('Enter a valid Delaware ZIP code for this comparison.')
        return zip5,zip_references()[zip5],False,'Saved center ZIP; Census ZIP-area reference'
    lat,lng=query.get('latitude'),query.get('longitude')
    if lat not in (None,'') and lng not in (None,''):
        try:point=(float(lat),float(lng))
        except (ValueError,TypeError):raise ValueError('Invalid center coordinates.') from None
        if any(not math.isfinite(x) or abs(x)>limit for x,limit in zip(point,(90,180))):raise ValueError('Invalid center coordinates.')
        basis='Nearest Delaware ZIP to the center’s saved coordinates'
    else:
        city=str(query.get('city','')).strip().split(',')[0]
        matches=[r for r in geo.lookup(city+', DE') if r['match']=='exact' and r['kind']=='place']
        if len(matches)!=1:raise ValueError('Save a Delaware ZIP code to compare nearby hospital prices.')
        point=(matches[0]['latitude'],matches[0]['longitude']);basis='Delaware city ZIP reference; center coordinates unavailable'
    import chr_index
    zip5=min(zip_references(),key=lambda z:chr_index.distance(point,dict(zip(('latitude','longitude'),zip_references()[z]))))
    return zip5,zip_references()[zip5],True,basis

QUERY="""SELECT place_id,MAX(ccn) AS ccn,MAX(hospital) AS hospital_name,MAX(city) AS city,MAX(zip5) AS zip5,modality AS exam,
 MIN(cash_price) AS self_pay_min,MAX(cash_price) AS self_pay_max,
 MIN(gross_charge) AS gross_charge,MIN(insured_min) AS insured_min,MAX(insured_max) AS insured_max,
 COUNT(DISTINCT cpt_code) AS codes_priced,MAX(latitude) AS latitude,MAX(longitude) AS longitude
 FROM public.de_hospital_imaging_prices
 WHERE contrast='without' AND lower(setting) IN ('outpatient','both')
 AND lower(billing_class) IN ('facility','hospital') AND professional IS DISTINCT FROM TRUE
 GROUP BY place_id,modality ORDER BY place_id,modality"""

def compare(query):
    if query.get("view")=="hospitals":
        from chr_delaware_six import prices
        return prices(query)
    import chr_index
    from postgres_store import PostgresConnection
    _,thresholds=chr_index.parameters({**query,'zip':str(query.get('zip','')).strip() or '00000'})
    zip5,point,fallback,basis=origin(query)
    with PostgresConnection(chr_index.database_url()) as db:
        db.execute('SET LOCAL transaction_read_only=on');db.execute("SET LOCAL statement_timeout='8s'")
        records=db.execute(QUERY).fetchall()
    rows=[];ccns={}
    for record in records:
        row={**record};ccns[row['place_id']]=row['ccn'];row['ccn']=row['place_id']
        row['distance_miles']=chr_index.distance(point,row)
        if row['distance_miles'] is not None and row['distance_miles']<=50:rows.append(row)
    result=chr_index.build_result(rows,zip5,thresholds,point)
    for hospital in result['hospitals']:
        hospital['id']=hospital['ccn'];hospital['ccn']=ccns[hospital['id']]
    result.update(zip_basis=basis,zip_fallback=fallback,state='DE',price_basis='Grouped noncontrast outpatient/both-setting facility or hospital-billed source rows. Hospital distances use Census ZIP-area reference points, not building locations. Confirm campus applicability where a source file covers multiple hospitals.')
    return result
