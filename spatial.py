import hashlib
import json
import math
import re
from pathlib import Path
import h3
import core
from proximity import offer_locations


ENTRY_FIELDS = ('name', 'area', 'states', 'entity', 'category', 'mri', 'ct', 'other', 'coverage', 'notes', 'source', 'additional', 'checked', 'is_test_center')
OFFER_FIELDS = ('id', 'mod', 'contrast', 'type', 'region', 'scope', 'included', 'order', 'exam_terms')
BUNDLE_LIMIT = 500


def initialize(db):
    db.executescript((Path(__file__).parent / 'ops/sql/20261101-spatial.sql').read_text())


def prepare_location(location):
    lat, lng = location.get('latitude'), location.get('longitude')
    if location.get('precision') not in ('address', 'zip', 'city'):
        return None
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > limit for value, limit in ((lat, 90), (lng, 180))):
        return None
    safe = {key: location[key] for key in ('city', 'state', 'zip', 'latitude', 'longitude', 'precision') if key in location}
    return {**safe, 'h3_res7': h3.latlng_to_cell(lat, lng, 7), 'h3_res11': h3.latlng_to_cell(lat, lng, 11)}


def index_entry(db, batch, key, entry):
    db.execute('DELETE FROM spatial_locations WHERE batch_id=? AND entry_key=?', (batch, key))
    if entry.get('location_type') == 'program':
        return 0
    rows = []
    for ordinal, source in enumerate(entry.get('locations', [])):
        if not isinstance(source, dict):
            continue
        location = prepare_location(source)
        if location is None:
            continue
        ident = hashlib.sha256(json.dumps([batch, key, ordinal]).encode()).hexdigest()
        rows.append((ident, batch, key, ordinal, location['h3_res7'], location['h3_res11'], json.dumps(location, separators=(',', ':'))))
    if rows:
        db.executemany('INSERT INTO spatial_locations(id,batch_id,entry_key,ordinal,h3_res7,h3_res11,location) VALUES(?,?,?,?,?,?,?)', rows)
    return len(rows)


def index_batch(db, batch):
    last, count = '', 0
    while True:
        rows = db.execute('SELECT entry_key,payload FROM entries WHERE batch_id=? AND entry_key>? ORDER BY entry_key LIMIT 200', (batch, last)).fetchall()
        if not rows:
            break
        for row in rows:
            count += index_entry(db, batch, row['entry_key'], json.loads(row['payload']))
        last = rows[-1]['entry_key']
    db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('spatial-ready:' + batch, '1'))
    return count


def validate_cell(cell):
    if not isinstance(cell, str) or not re.fullmatch(r'[0-9a-f]{15}', cell) or not h3.is_valid_cell(cell) or h3.get_resolution(cell) != 7:
        raise ValueError('Enter a valid H3 Resolution 7 cell ID.')
    return cell


def bundle(path, cell):
    validate_cell(cell)
    cells = sorted(h3.grid_disk(cell, 1))
    placeholders = ','.join('?' for _ in cells)
    with core.connect(path) as db:
        batch = core.active_batch(db)
        if not batch:
            return {'center_cell': cell, 'queried_cells': cells, 'count': 0, 'records': [], 'version': None, 'truncated': False, 'limit': BUNDLE_LIMIT}
        if not db.execute('SELECT value FROM settings WHERE key=?', ('spatial-ready:' + batch,)).fetchone():
            raise SpatialNotReady()
        query = f'FROM spatial_locations s JOIN entries e ON e.batch_id=s.batch_id AND e.entry_key=s.entry_key WHERE s.batch_id=? AND s.h3_res7 IN ({placeholders})'
        params = (batch, *cells)
        count = db.execute('SELECT COUNT(*) AS n ' + query, params).fetchone()['n']
        rows = db.execute('SELECT s.id,s.entry_key,s.h3_res7,s.h3_res11,s.location,e.payload ' + query + ' ORDER BY s.id LIMIT ?', (*params, BUNDLE_LIMIT)).fetchall()
        keys = sorted({row['entry_key'] for row in rows})
        offers = {}
        if keys:
            marks = ','.join('?' for _ in keys)
            offer_rows = db.execute(f'SELECT entry_key,payload,amount_cents,max_cents FROM offers WHERE batch_id=? AND entry_key IN ({marks}) ORDER BY id LIMIT 1001', (batch, *keys)).fetchall()
            for item in offer_rows[:1000]:
                offers.setdefault(item['entry_key'], []).append(item)
            offers_truncated = len(offer_rows) > 1000
        else:
            offers_truncated = False
    records = []
    for row in rows:
        source = json.loads(row['payload'])
        location = json.loads(row['location'])
        entry = {key: source[key] for key in ENTRY_FIELDS if key in source}
        entry.update(booking_id=row['entry_key'], locations=[location], location_type='site')
        mapped = []
        for item in offers.get(row['entry_key'], []):
            raw = json.loads(item['payload'])
            sites = offer_locations({**raw, 'entry': source})
            if not any(site.get('latitude') == location['latitude'] and site.get('longitude') == location['longitude'] for site in sites):
                continue
            offer = {key: raw[key] for key in OFFER_FIELDS if key in raw}
            offer.update(amount=item['amount_cents'] / 100 if item['amount_cents'] is not None else None)
            if item['max_cents'] is not None:
                offer['max'] = item['max_cents'] / 100
            mapped.append(offer)
        records.append({'id': row['id'], 'entry_key': row['entry_key'], 'h3_res7': row['h3_res7'], 'h3_res11': row['h3_res11'], 'location': location, 'entry': entry, 'offers': mapped})
    return {'center_cell': cell, 'queried_cells': cells, 'count': count, 'records': records, 'version': batch, 'truncated': count > len(records), 'offers_truncated': offers_truncated, 'limit': BUNDLE_LIMIT}


class SpatialNotReady(Exception):
    pass
