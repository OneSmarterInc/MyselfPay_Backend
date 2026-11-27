"""Read-only CHR comparisons against the separately configured hospital database."""
import math
import os
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

QUERY = 'SELECT * FROM public.hospitals_near(%s, %s, %s, %s)'


def money(value):
    if value in (None, ''):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Enter valid MRI and CT prices.') from None
    if not number.is_finite() or number < 0 or number > Decimal('999999.99') or number.as_tuple().exponent < -2:
        raise ValueError('Prices must be between $0.00 and $999,999.99, with at most two decimals.')
    return number


def parameters(query):
    zip5 = str(query.get('zip', '')).strip()
    if len(zip5) != 5 or not zip5.isascii() or not zip5.isdigit():
        raise ValueError('Enter a valid five-digit US ZIP code.')
    thresholds = {key: money(query.get(key.lower())) for key in ('MRI', 'CT')}
    if all(value is None for value in thresholds.values()):
        raise ValueError('Enter an MRI price, a CT price, or both.')
    return zip5, thresholds


def distance(origin, row):
    if origin is None or any(value is None for value in (*origin, row.get('latitude'), row.get('longitude'))):
        return None
    lat1, lng1, lat2, lng2 = map(math.radians, (*origin, float(row['latitude']), float(row['longitude'])))
    value = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lng2-lng1)/2)**2
    return round(3958.7613 * 2 * math.asin(math.sqrt(min(1, max(0, value)))), 1)


def numeric(value):
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def build_result(rows, zip5, thresholds, origin=None):
    hospitals = {}
    matches = {'MRI': 0, 'CT': 0}
    for row in rows:
        exam = row['exam']
        threshold = thresholds.get(exam)
        price = row['self_pay_min']
        if threshold is None or price is None or not Decimal(str(price)).is_finite() or Decimal(str(price)) <= threshold:
            continue
        item = hospitals.setdefault(row['ccn'], {
            'ccn': row['ccn'], 'hospital_name': row['hospital_name'], 'city': row['city'], 'zip5': row['zip5'],
            'distance_miles': float(row['distance_miles']) if row.get('distance_miles') is not None else distance(origin, row), 'price_file_url': row.get('price_file_url'),
            'price_page_url': row.get('price_page_url'), 'prices': {},
        })
        item['prices'][exam] = {
            'cash_price': float(price), 'difference': float(Decimal(str(price))-threshold),
            **{key: numeric(row.get(key)) for key in ('self_pay_min','self_pay_max','gross_min','gross_charge','insured_min','insured_median','insured_max')},
            'codes_priced': row.get('codes_priced', 0),
        }
        matches[exam] += 1
    values = sorted(hospitals.values(), key=lambda row: (row['distance_miles'] is None, row['distance_miles'] if row['distance_miles'] is not None else 0, row['hospital_name'], row['ccn']))
    return {'zip': zip5, 'hospital_count': len(values), 'matches': matches,
            'thresholds': {key: float(value) if value is not None else None for key,value in thresholds.items()},
            'distance_basis': 'ZIP centroid; approximate straight-line miles' if origin is not None else None,
            'price_basis': 'Median cash price across the databaseâ€™s grouped noncontrast procedure codes.',
            'hospitals': values}


def resolve_ohio_zip(db, query):
    if str(query.get('state', '')).strip().upper() not in ('OH', 'OHIO'):
        raise ValueError('This center has no saved ZIP code. Hospital comparison is unavailable.')
    lat, lng = query.get('latitude'), query.get('longitude')
    if lat not in (None, '') and lng not in (None, ''):
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            raise ValueError('Center coordinates are invalid.') from None
        if not math.isfinite(lat) or not math.isfinite(lng) or abs(lat)>90 or abs(lng)>180:
            raise ValueError('Center coordinates are invalid.')
        row = db.execute("""SELECT zip5 FROM public.zip_centroid
            WHERE state='OH' AND latitude IS NOT NULL AND longitude IS NOT NULL
            ORDER BY (sin(radians(latitude-%s)/2)^2 + cos(radians(%s))*cos(radians(latitude))*sin(radians(longitude-%s)/2)^2),zip5 LIMIT 1""", (lat,lat,lng)).fetchone()
        basis = 'Nearest Ohio ZIP to the center’s stored coordinates'
    else:
        city = str(query.get('city', '')).strip()[:120]
        if not city:
            raise ValueError('This Ohio center needs a saved city or coordinates to find a ZIP reference.')
        row = db.execute("""SELECT zip5 FROM public.zip_centroid WHERE state='OH'
            AND lower(city)=lower(%s) ORDER BY
            (latitude-(SELECT avg(latitude) FROM public.zip_centroid WHERE state='OH' AND lower(city)=lower(%s)))^2+
            (longitude-(SELECT avg(longitude) FROM public.zip_centroid WHERE state='OH' AND lower(city)=lower(%s)))^2,zip5 LIMIT 1""",(city,city,city)).fetchone()
        basis = 'Ohio city ZIP reference; center coordinates unavailable'
    if not row:
        raise ValueError('No Ohio ZIP reference was found for this center’s saved location.')
    return row['zip5'], basis


def database_url():
    target = os.getenv('CHR_DATABASE_URL', '')
    if not target:
        raise RuntimeError('CHR database is not configured')
    parsed = urlsplit(target)
    if parsed.scheme not in ('postgres', 'postgresql') or parsed.path.lstrip('/') != 'selfpay_hospital_compare_v10':
        raise RuntimeError('Unexpected CHR database configuration')
    return target


def compare(query):
    query = query.dict() if hasattr(query, 'dict') else dict(query)
    if str(query.get('state','')).strip().upper() in ('CA','CALIFORNIA'):
        from chr_california import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('NV','NEVADA'):
        from chr_nevada import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('MN','MINNESOTA'):
        from chr_minnesota import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('MA','MASSACHUSETTS'):
        from chr_massachusetts import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('MS','MISSISSIPPI'):
        from chr_mississippi import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('AZ','ARIZONA'):
        from chr_arizona import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('ME','MAINE'):
        from chr_maine import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('OR','OREGON'):
        from chr_oregon import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('NY','NEW YORK'):
        from chr_new_york import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('WA','WASHINGTON'):
        from chr_washington import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('NH','NEW HAMPSHIRE'):
        from chr_new_hampshire import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('VA','VIRGINIA'):
        from chr_virginia import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('PR','PUERTO RICO'):
        from chr_puerto_rico import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('SC','SOUTH CAROLINA'):
        from chr_south_carolina import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('NJ','NEW JERSEY'):
        from chr_new_jersey import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('ID','IDAHO'):
        from chr_idaho import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('NE','NEBRASKA'):
        from chr_nebraska import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('CT','CONNECTICUT'):
        from chr_connecticut import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('AS','AMERICAN SAMOA'):
        from chr_american_samoa import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('GA','GEORGIA'):
        from chr_georgia import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('IA','IOWA'):
        from chr_iowa import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('WI','WISCONSIN'):
        from chr_wisconsin import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('MT','MONTANA'):
        from chr_montana import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('OK','OKLAHOMA'):
        from chr_oklahoma import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('SD','SOUTH DAKOTA'):
        from chr_south_dakota import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('DC','DISTRICT OF COLUMBIA'):
        from chr_district_of_columbia import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('TN','TENNESSEE'):
        from chr_tennessee import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('RI','RHODE ISLAND'):
        from chr_rhode_island import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('ND','NORTH DAKOTA'):
        from chr_north_dakota import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('KY','KENTUCKY'):
        from chr_kentucky import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('AL','ALABAMA'):
        from chr_alabama import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('IN','INDIANA'):
        from chr_indiana import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('IL','ILLINOIS'):
        from chr_illinois import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('NM','NEW MEXICO'):
        from chr_new_mexico import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('AR','ARKANSAS'):
        from chr_arkansas import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('CO','COLORADO'):
        from chr_colorado import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('HI','HAWAII'):
        from chr_hawaii import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('MI','MICHIGAN'):
        from chr_michigan import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('LA','LOUISIANA'):
        from chr_louisiana import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('PA','PENNSYLVANIA'):
        from chr_pennsylvania import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('AK','ALASKA'):
        from chr_alaska import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('VT','VERMONT'):
        from chr_vermont import prices
        return prices(query)
    if str(query.get('state','')).strip().upper() in ('DE','DELAWARE'):
        from chr_delaware import compare as delaware_compare
        return delaware_compare(query)
    filtered=query.get('view')=='filtered'
    if filtered:
        from chr_ranges import reference,OHIO_QUERY
        threshold,basis=reference(query)
        query={**query,'mri':str(threshold),'ct':str(threshold)}
    missing_zip = not str(query.get('zip', '')).strip()
    zip5, thresholds = parameters({**query, 'zip': '00000' if missing_zip else query.get('zip')})
    zip_basis = 'Saved center ZIP'
    target = database_url()
    with psycopg.connect(target, connect_timeout=5, row_factory=dict_row,
                         options='-c default_transaction_read_only=on -c statement_timeout=8000') as db:
        if missing_zip:
            zip5, zip_basis = resolve_ohio_zip(db, query)
        center = db.execute('SELECT latitude,longitude FROM public.zip_centroid WHERE zip5=%s', (zip5,)).fetchone()
        origin = (center['latitude'], center['longitude']) if center else None
        rows = []
        for exam, threshold in thresholds.items():
            if threshold is not None:
                for row in db.execute(OHIO_QUERY if filtered else QUERY, (zip5, exam, threshold, 50)).fetchall():
                    rows.append({**row, 'exam': exam, 'distance_miles': row.get('miles')})
    result = build_result(rows, zip5, thresholds, origin)
    if filtered:result.update(threshold_basis=basis,range_screened=True)
    result['zip_basis'] = zip_basis
    result['zip_fallback'] = missing_zip
    return result
