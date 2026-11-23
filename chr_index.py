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
            'price_basis': 'Median cash price across the database’s grouped noncontrast procedure codes.',
            'hospitals': values}


def compare(query):
    zip5, thresholds = parameters(query)
    target = os.getenv('CHR_DATABASE_URL', '')
    if not target:
        raise RuntimeError('CHR database is not configured')
    parsed = urlsplit(target)
    if parsed.scheme not in ('postgres', 'postgresql') or parsed.path.lstrip('/') != 'selfpay_hospital_compare_v10':
        raise RuntimeError('Unexpected CHR database configuration')
    with psycopg.connect(target, connect_timeout=5, row_factory=dict_row,
                         options='-c default_transaction_read_only=on -c statement_timeout=8000') as db:
        center = db.execute('SELECT latitude,longitude FROM public.zip_centroid WHERE zip5=%s', (zip5,)).fetchone()
        origin = (center['latitude'], center['longitude']) if center else None
        rows = []
        for exam, threshold in thresholds.items():
            if threshold is not None:
                for row in db.execute(QUERY, (zip5, exam, threshold, 50)).fetchall():
                    rows.append({**row, 'exam': exam, 'distance_miles': row.get('miles')})
    return build_result(rows, zip5, thresholds, origin)
