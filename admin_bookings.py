"""Read-only booking views for the superuser-only MySelfPay admin workspace."""
import time
from datetime import datetime, timezone
from django.conf import settings
import booking

LABELS={'requested':'Awaiting center approval','voicemail':'Center follow-up','scheduled':'Approved','unable':'Declined','expired':'Expired'}
def date_text(value):
    return datetime.fromtimestamp(value,timezone.utc).strftime('%d %b %Y, %H:%M UTC') if value else '—'

def stats():
    with booking.connection(settings.DOMAIN_DATABASE) as db:
        rows=db.execute("SELECT status,COUNT(*) AS n FROM booking_requests GROUP BY status").fetchall()
        counts={r['status']:r['n'] for r in rows}
    return {'request_total':sum(counts.values()),'request_approved':counts.get('scheduled',0)}

def page(number,mode):
    where=" WHERE status='scheduled'" if mode=='approved' else ''
    with booking.connection(settings.DOMAIN_DATABASE) as db:
        total=db.execute('SELECT COUNT(*) AS n FROM booking_requests'+where).fetchone()['n']
        pages=max(1,(total+24)//25);number=min(max(1,number),pages)
        rows=db.execute('SELECT * FROM booking_requests'+where+' ORDER BY created DESC,id DESC LIMIT ? OFFSET ?',(25,(number-1)*25)).fetchall()
        items=[]
        for row in rows:
            payload=booking.unpack(row)
            status='expired' if row['status'] not in booking.TERMINAL and row['link_expires']<=time.time() else row['status']
            items.append({'id':row['id'],'clinic':payload['clinic_name'],'patient':payload['first_name'],
                          'phone':payload.get('phone',''),'status':LABELS[status],
                          'submitted':date_text(row['created']),'approved':date_text(row['closed']) if status=='scheduled' else '—'})
    return {'requests':items,'page':number,'pages':pages,'total':total,'mode':mode,
            'previous':number-1 if number>1 else None,'next':number+1 if number<pages else None}

def detail(ident):
    from mail_diagnostics import request_status
    with booking.connection(settings.DOMAIN_DATABASE) as db:
        row=booking.get_row(db,ident)
        data=booking.unpack(row)
        status='expired' if row['status'] not in booking.TERMINAL and row['link_expires']<=time.time() else row['status']
        return {'mail_diagnostic': request_status(settings.DOMAIN_DATABASE, ident), 'id':row['id'],'status':LABELS[status],'clinic':data['clinic_name'],
                'location':data.get('location',''),'center_email':data['recipient'],
                'first_name':data['first_name'],'phone':data.get('phone',''),
                'service':data['service'],'exam':data.get('exam',''),'contrast':data.get('contrast','unknown'),
                'timezone':booking.ZONES[data['timezone']], 'price':data['price_text'],
                'slots':[{'date':s['date'],'period':booking.PERIODS[s['period']]} for s in data['slots']],
                'submitted':date_text(row['created']),'approved':date_text(row['closed']) if status=='scheduled' else '—',
                'closed':date_text(row['closed']),'delivery':booking.booking_delivery.status(db,row['id'],row['delivery']),
                'consent':data.get('consent',False),'test_center':data.get('is_test_center',False)}
