"""Review center-entered prices. Approving publishes the price as a new directory batch.

The previous batch is kept, so a bad approval can be rolled back with core.publish(previous_batch).
"""
import json
import re
import time
import uuid
from datetime import date
import core

CONTRAST = {'without': 'without', 'with': 'with', 'either': 'both', 'unknown': 'unknown'}
CONTRAST_TEXT = {'without': 'without contrast', 'with': 'with contrast', 'both': 'with or without contrast', 'unknown': 'contrast not stated'}
READING = {'yes': 'Radiologist reading included.', 'no': 'Radiologist reading billed separately.', 'unknown': 'Radiologist reading: ask the center.'}


OTHER_EXAMS = ('Ultrasound', 'X-ray')


def other_text(current, exam, amount, today):
    """Add the center's price to the listing's 'Other imaging' text. A newer price for the same exam replaces the older one."""
    kept = re.sub(r'\s*' + re.escape(exam) + r': \$[\d,.]+ \(confirmed by center[^)]*\)\.?', '', str(current or '')).strip()
    line = f'{exam}: ${amount:,.2f} (confirmed by center {today}).'.replace('.00 (', ' (')
    return (kept + ' ' + line).strip()


class PriceError(ValueError):
    pass


def pending(path, status='pending'):
    with core.connect(path) as db:
        active=core.active_batch(db)
        rows=db.execute("SELECT p.*,e.payload AS entry_payload FROM center_price_submissions p LEFT JOIN entries e "
                        "ON e.batch_id=? AND e.entry_key=p.entry_key WHERE p.status=? ORDER BY p.submitted DESC LIMIT 200",(active,status)).fetchall()
        out=[]
        for row in rows:
            item=dict(row);payload=item.pop('entry_payload')
            item.update(center=json.loads(payload).get('name',item['entry_key']) if payload else item['entry_key'],
                        amount=f"{item['amount_cents']/100:,.2f}",type_text='Starting at' if item['price_type']=='from' else 'Fixed',
                        sent_text=__import__('admin_alerts').when(item['submitted']),decided_text=__import__('admin_alerts').when(item['decided']))
            out.append(item)
        return out



def reject(path, ident, by):
    if not by.strip():
        raise PriceError('Reviewer name is required.')
    with core.connect(path) as db:
        sub = db.execute("SELECT entry_key,exam,amount_cents,contrast FROM center_price_submissions WHERE id=? AND status='pending'", (ident,)).fetchone()
        if not sub or not db.execute("UPDATE center_price_submissions SET status='rejected',decided=?,decided_by=? WHERE id=? AND status='pending'",
                                     (time.time(), by.strip(), ident)).rowcount:
            raise PriceError('No pending submission with that id.')
        import audit
        audit.log('admin.price.reject', scope=sub['entry_key'], target_type='price', target_id=ident, actor=by.strip(), actor_type='admin',
                  new=f"{sub['exam']} ${sub['amount_cents'] / 100:g} ({sub['contrast']})")


def approve(path, ident, by):
    """Mark approved and publish a new batch holding the price. Returns the new and previous batch ids."""
    if not by.strip():
        raise PriceError('Reviewer name is required.')
    with core.connect(path) as db:
        sub = db.execute("SELECT * FROM center_price_submissions WHERE id=? AND status='pending'", (ident,)).fetchone()
        if not sub:
            raise PriceError('No pending submission with that id.')
        active = core.active_batch(db)
        entry = db.execute('SELECT content_hash,payload FROM entries WHERE batch_id=? AND entry_key=?', (active, sub['entry_key'])).fetchone()
        if not entry:
            raise PriceError('This listing is not in the published directory.')
        center = json.loads(entry['payload'])
        contrast = CONTRAST.get(sub['contrast'], 'unknown')
        other = sub['exam'] in OTHER_EXAMS
        if other:
            contrast = 'unknown'
        today = date.today().isoformat()
        loc = (center.get('locations') or [{}])[0]
        region = ', '.join(x for x in (loc.get('city') or center.get('area'), loc.get('state')) if x)
        offer = {
            'id': 'center-' + ident, 'entry_name': center.get('name'), 'entry_hash': entry['content_hash'],
            'mod': sub['exam'], 'contrast': contrast, 'type': 'fixed' if sub['price_type'] == 'fixed' else 'starting',
            'amount': sub['amount_cents'] / 100, 'region': region, 'interpretation_included': sub['interpretation_included'],
            'scope': sub['exam'] if other else f"{sub['exam']} {CONTRAST_TEXT[contrast]}",
            'included': READING.get(sub['interpretation_included'], READING['unknown']),
            'order': sub['note'] or '', 'source': f'Price confirmed by center, {today}',
            'confirmed_by_center': today, 'updated_at': time.time(), 'submission_id': ident,
        }
        old = db.execute('SELECT id,payload FROM offers WHERE batch_id=? AND entry_key=?', (active, sub['entry_key'])).fetchall()
        keep, replaced_prices = [], []
        for r in old:
            p = json.loads(r['payload'])
            replaced = p.get('mod') == sub['exam'] and (p.get('contrast') == contrast or p.get('type') == 'quote')
            if not replaced:
                keep.append(r['id'])
            else:
                replaced_prices.append(f"{p.get('mod')} ${p.get('amount')} ({p.get('contrast')})")
        batch = str(uuid.uuid4())
        signature = core.digest({'parent': active, 'center_price': ident})
        db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)', (batch, signature, 'Center price ' + ident, time.time(), 'staged', '{}'))
        db.execute('INSERT INTO entries(batch_id,entry_key,content_hash,payload) SELECT ?,entry_key,content_hash,payload FROM entries WHERE batch_id=?', (batch, active))
        if other:
            center['other'] = other_text(center.get('other'), sub['exam'], sub['amount_cents'] / 100, today)
            db.execute('DELETE FROM entries WHERE batch_id=? AND entry_key=?', (batch, sub['entry_key']))
            db.execute('INSERT INTO entries VALUES(?,?,?,?)', (batch, sub['entry_key'], core.digest(center), core.canonical(center)))
        db.execute('INSERT INTO offers(batch_id,id,entry_key,amount_cents,max_cents,price_type,payload) '
                   'SELECT ?,id,entry_key,amount_cents,max_cents,price_type,payload FROM offers WHERE batch_id=? AND entry_key<>?', (batch, active, sub['entry_key']))
        for oid in keep:
            db.execute('INSERT INTO offers(batch_id,id,entry_key,amount_cents,max_cents,price_type,payload) '
                       'SELECT ?,id,entry_key,amount_cents,max_cents,price_type,payload FROM offers WHERE batch_id=? AND id=?', (batch, active, oid))
        db.execute('INSERT INTO offers VALUES(?,?,?,?,?,?,?)', (batch, offer['id'], sub['entry_key'], sub['amount_cents'], None, offer['type'], core.canonical(offer)))
        import spatial
        spatial.clone_index(db, active, batch)
        count = db.execute('SELECT COUNT(*) AS n FROM offers WHERE batch_id=?', (batch,)).fetchone()['n']
        db.execute("UPDATE batches SET status='published',report=? WHERE id=?",
                   (core.canonical({'parent_batch': active, 'center_price': ident, 'approved_by': by.strip(), 'offer_count': count}), batch))
        db.execute("INSERT INTO settings VALUES('active_batch',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (batch,))
        db.execute('INSERT INTO publish_events(batch_id,previous_batch,created,action) VALUES(?,?,?,?)', (batch, active, time.time(), 'publish'))
        db.execute("UPDATE center_price_submissions SET status='approved',decided=?,decided_by=? WHERE id=?", (time.time(), by.strip(), ident))
        import audit
        audit.log('admin.price.approve', scope=sub['entry_key'], target_type='price', target_id=ident, actor=by.strip(), actor_type='admin',
                  old='; '.join(replaced_prices) or 'no earlier price', new=f"{sub['exam']} ${sub['amount_cents'] / 100:g} ({contrast})",
                  detail={'batch': batch, 'previous_batch': active})
    return {'batch': batch, 'previous_batch': active}
