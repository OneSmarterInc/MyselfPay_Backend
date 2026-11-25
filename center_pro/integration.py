"""Stable mappings to the existing directory and encrypted booking store."""
import json, re, secrets, time
from decimal import Decimal
from datetime import datetime, timezone as utc
from zoneinfo import ZoneInfo
from django.db import connection, transaction
from django.conf import settings
from .models import Center, Offer, BookingRequest, Slot

class Result:
    def __init__(self,rows):self.rows=rows
    def fetchone(self):return self.rows[0] if self.rows else None
    def fetchall(self):return self.rows

class DomainConnection:
    # Existing domain operations participate in the SAME Django transaction.
    def execute(self,sql,params=()):
        with connection.cursor() as cursor:
            if connection.vendor=='sqlite':sql=sql.replace(' FOR UPDATE','')
            cursor.execute(sql.replace('?','%s'),params)
            rows=[dict(zip([c[0] for c in cursor.description],row)) for row in cursor.fetchall()] if cursor.description else []
        return Result(rows)


def sync_directory():
    import core, booking
    from directory_policy import visible_sql
    db=DomainConnection();batch=core.active_batch(db)
    predicate,values=visible_sql()
    rows=db.execute('SELECT entry_key,payload FROM entries WHERE batch_id=? AND '+predicate,(batch,*values)).fetchall()
    keys=[]
    for row in rows:
        e=json.loads(row['payload']);key=row['entry_key'];keys.append(key)
        Center.objects.update_or_create(directory_key=key,defaults={'name':e.get('name',key)[:180],'timezone':booking.default_timezone(e),'directory_active':True})
    Center.objects.filter(directory_key__isnull=False).exclude(directory_key__in=keys).update(directory_active=False)
    for row in db.execute('SELECT id,entry_key,amount_cents,payload FROM offers WHERE batch_id=? AND amount_cents IS NOT NULL',(batch,)).fetchall():
        e=json.loads(row['payload'])
        if e.get('type') not in ('fixed','starting') or not row['amount_cents'] or row['amount_cents']<=0:continue
        center=Center.objects.filter(directory_key=row['entry_key'],directory_active=True).first()
        if not center:continue
        exam=' · '.join(filter(None,[e.get('mod'),e.get('scope'),e.get('contrast')]))[:180]
        # Offers are authoritative only when mapped to a published numerical offer.
        existing=Offer.objects.filter(center=center,exam=exam).first()
        if existing and existing.directory_offer_id!=row['id']:continue
        Offer.objects.get_or_create(directory_offer_id=row['id'],defaults={'center':center,'exam':exam or 'Imaging','price':Decimal(row['amount_cents'])/100})
    return len(keys)


def sync_requests(center):
    if not center.directory_key:return
    import booking
    db=DomainConnection()
    rows=db.execute('SELECT b.* FROM booking_requests b JOIN center_request_routes r ON r.request_id=b.id WHERE r.clinic_id=? ORDER BY b.created DESC',(center.directory_key,)).fetchall()
    existing=BookingRequest.objects.filter(center=center,legacy_id__in=[row['id'] for row in rows]).in_bulk(field_name='legacy_id')
    for row in rows:
        closed_status=row['status'] in ('cancelled','unable','expired')
        if (row['closed'] or row['link_expires'])+30*86400<=time.time():continue
        if not closed_status and row['status']!='scheduled' and row['link_expires']<=time.time():
            closed_status=True
        status='closed' if closed_status else 'confirmed' if row['status']=='scheduled' else 'pending'
        item=existing.get(row['id'])
        # Unchanged imported requests need neither decryption nor per-row ORM reads.
        if item and item.status==status and (row['status']!='scheduled' or item.slot_id):continue
        p=booking.unpack(row)
        if p.get('clinic_id')!=center.directory_key:continue
        amount=None
        # Only a complete historical price statement is a trustworthy numerical snapshot.
        text=p.get('price_text','')
        if not re.search(r'from|starting|range|quote|confirm|additional|plus',text,re.I):
            value=re.search(r'\$([\d,]+(?:\.\d{1,2})?)',text)
            if value:amount=Decimal(value[1].replace(',',''))
        defaults={'center':center,'reference':row['id'],'patient_name':p.get('first_name') or 'Patient',
          'exam':' · '.join(filter(None,[p.get('service'),p.get('exam')]))[:180] or 'Contact patient',
          'preferred_windows':p.get('slots',[]),'source':'myselfpay','price_snapshot':amount,
          'status':'closed' if closed_status else 'confirmed' if row['status']=='scheduled' else 'pending'}
        created=False
        if item is None:item,created=BookingRequest.objects.get_or_create(legacy_id=row['id'],defaults=defaults)
        if created:
            BookingRequest.objects.filter(pk=item.pk).update(created_at=datetime.fromtimestamp(row['created'],utc.utc))
        elif item.status!=defaults['status']:
            BookingRequest.objects.filter(pk=item.pk).update(status=defaults['status'])
        if row['status']=='scheduled' and not item.slot_id:
            choice=p.get('approved_slot') or {}
            if choice.get('date') and choice.get('time'):
                from datetime import timedelta
                at=datetime.fromisoformat(choice['date']+'T'+choice['time']).replace(tzinfo=ZoneInfo(p['timezone']))
                slot,_=Slot.objects.get_or_create(center=center,resource='Imported appointment '+row['id'],starts_at=at,defaults={'ends_at':at+timedelta(minutes=30)})
                BookingRequest.objects.filter(pk=item.pk).update(slot=slot)


def confirm_existing(booking_request,slot):
    if not booking_request.legacy_id:return
    import booking,appointment_slots,booking_chat
    db=DomainConnection();row=booking.get_row(db,booking_request.legacy_id,lock=True)
    payload=booking.unpack(row)
    if payload.get('clinic_id')!=booking_request.center.directory_key:
        from .services import Conflict
        raise Conflict('The linked request does not belong to this center.')
    if row['status'] in booking.TERMINAL:
        from .services import Conflict
        raise Conflict('This request has already closed. Refresh the queue.')
    at=slot.starts_at.astimezone(ZoneInfo(booking_request.center.timezone))
    row=appointment_slots.update(db,row,'scheduled',{'date':at.strftime('%Y-%m-%d'),'time':at.strftime('%H:%M')})
    booking_chat.enqueue(db,row)


def queue_price_review(offer,user,reason):
    if not offer.directory_offer_id or not offer.center.directory_key:return
    import booking,core
    db=DomainConnection();row=db.execute('SELECT payload FROM offers WHERE batch_id=? AND id=?',(core.active_batch(db),offer.directory_offer_id)).fetchone()
    if not row:raise ValueError('The published offer is no longer available. Refresh this center.')
    p=json.loads(row['payload']);exam=p.get('mod','MRI');contrast=p.get('contrast','unknown')
    if contrast not in ('with','without','unknown','either'):contrast='either' if contrast=='both' else 'unknown'
    db.execute("UPDATE center_price_submissions SET status='replaced',decided=? WHERE entry_key=? AND exam=? AND contrast=? AND status='pending'",(time.time(),offer.center.directory_key,exam,contrast))
    db.execute('INSERT INTO center_price_submissions(id,entry_key,email_hash,exam,amount_cents,price_type,contrast,interpretation_included,note,submitted) VALUES(?,?,?,?,?,?,?,?,?,?)',
      (secrets.token_urlsafe(12),offer.center.directory_key,booking.digest(user.email.lower()),exam,int(offer.price*100),'fixed',contrast,p.get('interpretation_included','unknown'),reason[:500],time.time()))
