"""Read-only hospital range screening shared by imported state datasets."""
from decimal import Decimal

def reference(query):
    from chr_index import money
    for key in ('mri','ct'):
        value=money(query.get(key))
        if value is not None and value>0:return value,key.upper()
    return Decimal('100'),'Default'


LEGACY_TABLES={'ND':'public.ky_hospitnd_imaging_prices','KY':'public.ky_hospitky_imaging_prices'}

def resolve_state_table(db,table,state):
    legacy=LEGACY_TABLES.get(state)
    if not legacy:return table
    row=db.execute('SELECT to_regclass(%s) AS canonical,to_regclass(%s) AS legacy',(table,legacy)).fetchone()
    return table if row['canonical'] else legacy if row['legacy'] else table

def hospital_summary(db,query,table,state,dataset):
    # Identifiers come only from the module-owned allowlist, never request text.
    allowed={'public.'+code+'_hospital_imaging_prices' for code in ('ca','dc','tn','ri','nd','ky','al','in','il','nm','ar','co','hi','mi','la','pa','ak','vt')}
    allowed.add('public.de_hospital_imaging_prices_six')
    if state in LEGACY_TABLES:allowed.add(LEGACY_TABLES[state])
    if table not in allowed:raise ValueError('Unsupported CHR table.')
    threshold,basis=reference(query)
    search=str(query.get('search','')).strip()[:120]
    where='WHERE hospital ILIKE %s' if search else ''
    params=('%'+search+'%',) if search else ()
    pattern='^[0-9]+([.][0-9]+)?( to [0-9]+([.][0-9]+)?)?$'
    rows=db.execute("""WITH source AS (
        SELECT hospital,cash_price,gross_charge,
         CASE WHEN insurer_rates ~ %s THEN split_part(insurer_rates,' to ',1)::numeric END AS insurer_low,
         CASE WHEN insurer_rates ~ %s THEN CASE WHEN strpos(insurer_rates,' to ')>0 THEN split_part(insurer_rates,' to ',2)::numeric ELSE insurer_rates::numeric END END AS insurer_high
        FROM """+table+' '+where+"""
        ),limits AS (SELECT %s::numeric AS threshold),summary AS (
        SELECT hospital AS hospital_name,COUNT(*) AS source_rows,
         MIN(cash_price) FILTER(WHERE cash_price>threshold) AS cash_min,MAX(cash_price) AS cash_max,
         MIN(gross_charge) FILTER(WHERE gross_charge>threshold) AS gross_min,MAX(gross_charge) AS gross_max,
         MIN((SELECT MIN(v) FROM (VALUES(insurer_low),(insurer_high)) AS candidates(v) WHERE v>threshold)) AS insured_min,
         MAX(insurer_high) AS insured_max
        FROM source CROSS JOIN limits GROUP BY hospital
        ) SELECT * FROM summary WHERE cash_min IS NOT NULL AND gross_min IS NOT NULL AND insured_min IS NOT NULL ORDER BY hospital_name""",(pattern,pattern,*params,threshold)).fetchall()
    hospitals=[{k:str(v) if isinstance(v,Decimal) else v for k,v in row.items()} for row in rows]
    return {'dataset':dataset,'state':state,'hospital_count':len(rows),'source_records':sum(r['source_rows'] for r in rows),'hospitals':hospitals,'threshold':str(threshold),'threshold_basis':basis}
# Same hospital/procedure eligibility as v_hospital_exam_group; preserve grouped maxima.
OHIO_QUERY="""WITH inputs AS (SELECT %s::text AS zip,%s::text AS exam,%s::numeric AS threshold,%s::numeric AS miles),
source AS (
 SELECT h.ccn,h.display_name AS hospital_name,h.zip5,h.city,h.latitude,h.longitude,h.price_file_url,h.price_page_url,g.exam,x.cpt,x.self_pay_min,x.self_pay_median,x.self_pay_max,x.gross_min,x.gross_median,x.gross_max,x.insured_min,x.insured_median,x.insured_max
 FROM public.hospital h JOIN public.hospital_exam_price x USING(ccn) JOIN public.exam_group_code g USING(cpt)
 CROSS JOIN inputs i WHERE h.is_shown AND x.is_shown AND x.self_pay_min IS NOT NULL AND g.exam=i.exam
),summary AS (
 SELECT s.ccn,s.hospital_name,s.zip5,s.city,s.latitude,s.longitude,s.price_file_url,s.price_page_url,s.exam,
 MIN((SELECT MIN(v) FROM (VALUES(s.self_pay_min),(s.self_pay_median),(s.self_pay_max)) AS c(v) WHERE v>i.threshold)) AS self_pay_min,
 MAX(s.self_pay_max) AS self_pay_max,
 MIN((SELECT MIN(v) FROM (VALUES(s.gross_min),(s.gross_median),(s.gross_max)) AS c(v) WHERE v>i.threshold)) AS gross_min,
 MAX(s.gross_max) AS gross_charge,
 MIN((SELECT MIN(v) FROM (VALUES(s.insured_min),(s.insured_median),(s.insured_max)) AS c(v) WHERE v>i.threshold)) AS insured_min,
 MAX(s.insured_max) AS insured_max,COUNT(*) AS codes_priced
 FROM source s CROSS JOIN inputs i GROUP BY s.ccn,s.hospital_name,s.zip5,s.city,s.latitude,s.longitude,s.price_file_url,s.price_page_url,s.exam
),located AS (
 SELECT s.*,3958.8*2*asin(sqrt(least(1::double precision,power(sin(radians(s.latitude-z.latitude)/2),2)+cos(radians(z.latitude))*cos(radians(s.latitude))*power(sin(radians(s.longitude-z.longitude)/2),2)))) AS miles
 FROM summary s CROSS JOIN inputs i JOIN public.zip_centroid z ON z.zip5=i.zip
) SELECT located.* FROM located CROSS JOIN inputs i WHERE located.miles<=i.miles
AND self_pay_min IS NOT NULL AND gross_min IS NOT NULL AND insured_min IS NOT NULL ORDER BY located.miles,hospital_name"""
