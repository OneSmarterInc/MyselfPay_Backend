"""Illinois source rows, kept distinct from exam-specific Ohio comparisons."""
import csv,hashlib
from decimal import Decimal,InvalidOperation
from pathlib import Path

FIELDS=['State','Hospital','Distance','Cash price','Gross charge','Insurer rates','CPT code']

def numeric(value,line):
    value=value.strip()
    if not value:return None
    try:number=Decimal(value)
    except InvalidOperation:raise ValueError(f'Row {line}: invalid numeric value.') from None
    if not number.is_finite() or number<0:raise ValueError(f'Row {line}: expected a nonnegative finite number.')
    return number

def source_rows(path):
    with open(path,encoding='utf-8-sig',newline='') as file:
        reader=csv.DictReader(file)
        if reader.fieldnames!=FIELDS:raise ValueError('Expected CSV columns: '+', '.join(FIELDS))
        for line,row in enumerate(reader,2):
            if None in row or any(value is None for value in row.values()):raise ValueError(f'Row {line}: inconsistent columns.')
            if row['State'].strip().upper()!='IL' or not row['Hospital'].strip():raise ValueError(f'Row {line}: Illinois state and hospital name are required.')
            yield (line,'IL',row['Hospital'].strip(),numeric(row['Distance'],line),numeric(row['Cash price'],line),numeric(row['Gross charge'],line),row['Insurer rates'].strip(),row)

def validate_source(path):
    count=0;hospitals=set()
    for row in source_rows(path):count+=1;hospitals.add(row[2])
    if not count:raise ValueError('The CSV has no records.')
    return count,len(hospitals),hashlib.sha256(Path(path).read_bytes()).hexdigest()

def prices(query):
    import chr_index
    from postgres_store import PostgresConnection
    try:page=int(query.get('page',1));size=int(query.get('page_size',20))
    except (TypeError,ValueError):raise ValueError('Provide valid page numbers.') from None
    if page<1 or page>1000000 or size not in (10,20,50):raise ValueError('Choose a valid page and page size of 10, 20 or 50.')
    search=str(query.get('search','')).strip()[:120]
    where='WHERE hospital ILIKE %s' if search else ''
    params=('%'+search+'%',) if search else ()
    with PostgresConnection(chr_index.database_url()) as db:
        db.execute('SET LOCAL transaction_read_only=on')
        db.execute("SET LOCAL statement_timeout='8s'")
        if query.get('view')=='hospitals':
            from chr_ranges import hospital_summary
            return hospital_summary(db,query,'public.il_hospital_imaging_prices','IL','illinois_hospital_summary')
        counts=db.execute('SELECT COUNT(*) AS count,COUNT(DISTINCT hospital) AS hospital_count FROM public.il_hospital_imaging_prices '+where,params).fetchone()
        rows=db.execute('SELECT source_row AS id,state,hospital,distance_miles,cash_price,gross_charge,insurer_rates FROM public.il_hospital_imaging_prices '+where+' ORDER BY hospital,source_row LIMIT %s OFFSET %s',(*params,size,(page-1)*size)).fetchall()
    return {'dataset':'illinois_statewide','state':'IL',**counts,'page':page,'page_size':size,'results':[{k:str(v) if isinstance(v,Decimal) else v for k,v in row.items()} for row in rows]}
