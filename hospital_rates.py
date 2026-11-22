"""Hospital published rates near a ZIP. Reads the hospital_rates table; never geocodes or calls out."""
import csv
import re
import geo
import proximity

EXAMS = {
    '70551': 'MRI brain without contrast', '73721': 'MRI knee without contrast',
    '72148': 'MRI lumbar spine without contrast', '70450': 'CT head without contrast',
    '71250': 'CT chest without contrast', '74177': 'CT abdomen and pelvis with contrast',
}
COLUMNS = ['cms_number', 'cpt', 'hospital_name', 'zip', 'city', 'latitude', 'longitude', 'exam',
           'cash_price', 'gross_charge', 'negotiated_low', 'negotiated_high', 'payer_count',
           'reading_low', 'reading_high', 'reading_included', 'shared_list_id', 'source_url', 'file_date', 'checked_on']
MAX_RADIUS = 50


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def load_csv(db, path, checked_on):
    """Replace every row with the review file's rows in one transaction. Blank means not posted."""
    rows = []
    with open(path, encoding='utf-8', newline='') as stream:
        for r in csv.DictReader(stream):
            if r['cpt'] not in EXAMS or not re.fullmatch(r'\d{5}', r['zip'] or ''):
                continue
            rows.append((r['cms_number'], r['cpt'], r['hospital_name'].strip(), r['zip'], r['city'],
                         float(r['latitude']) if r['latitude'] else None, float(r['longitude']) if r['longitude'] else None,
                         EXAMS[r['cpt']], _num(r['cash_price']), _num(r['gross_charge']),
                         _num(r['negotiated_low']), _num(r['negotiated_high']), int(float(r['payer_count'] or 0)),
                         _num(r['reading_negotiated_low']), _num(r['reading_negotiated_high']),
                         r['reading_included'] or 'Unknown', r['shared_list_id'] or None, None, None, checked_on))
    db.execute('DELETE FROM hospital_rates')
    db.executemany('INSERT INTO hospital_rates(' + ','.join(COLUMNS) + ') VALUES(' + ','.join('?' * len(COLUMNS)) + ')', rows)
    return len(rows)


def _range(rows, low_key, high_key=None):
    lows = [r[low_key] for r in rows if r[low_key] is not None]
    highs = [r[high_key or low_key] for r in rows if r[high_key or low_key] is not None]
    if not lows or not highs:
        return None
    return {'low': round(min(lows), 2), 'high': round(max(highs), 2), 'hospitals': len(lows)}


def nearby(db, zip5, cpt, radius=25):
    """Hospitals within radius miles of the ZIP with their figures, nearest first."""
    if cpt not in EXAMS or not re.fullmatch(r'\d{5}', str(zip5)):
        raise ValueError('A five-digit ZIP and one of the six exam codes are required.')
    radius = max(1, min(MAX_RADIUS, int(radius)))
    origin = next((c for c in geo.lookup(zip5) if c.get('kind') == 'zcta'), None)
    if not origin:
        raise LookupError('ZIP not found.')
    rows = db.execute("SELECT * FROM hospital_rates WHERE cpt=? AND latitude IS NOT NULL AND TRIM(hospital_name)<>'' AND (cash_price IS NOT NULL OR gross_charge IS NOT NULL OR negotiated_low IS NOT NULL)", (cpt,)).fetchall()
    found = []
    for r in rows:
        item = dict(r)
        item['distance_miles'] = round(proximity.miles(origin, {'latitude': item['latitude'], 'longitude': item['longitude']}), 1)
        if item['distance_miles'] <= radius:
            found.append(item)
    found.sort(key=lambda x: (x['distance_miles'], x['hospital_name']))
    systems = {x['shared_list_id'] or x['cms_number'] for x in found}
    return {
        'zip': zip5, 'cpt': cpt, 'exam': EXAMS[cpt], 'radius_miles': radius,
        'hospital_count': len(found), 'system_count': len(systems),
        'ranges': {'cash': _range(found, 'cash_price'), 'gross': _range(found, 'gross_charge'),
                   'insurer': _range(found, 'negotiated_low', 'negotiated_high')},
        'checked_on': max((x['checked_on'] for x in found if x['checked_on']), default=None),
        'reading_note': "Hospital figures may not include the radiologist's reading.",
        'hospitals': [{k: x[k] for k in ('cms_number', 'hospital_name', 'city', 'zip', 'distance_miles', 'cash_price', 'gross_charge',
                                          'negotiated_low', 'negotiated_high', 'payer_count', 'reading_included', 'shared_list_id', 'source_url')} for x in found],
    }
