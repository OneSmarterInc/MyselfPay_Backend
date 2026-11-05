"""Administrator-only delivery evidence; never returns tokens or patient content."""
import time
import booking
import booking_chat
import booking_delivery
import booking_mail


def request_status(path, ident):
    config = booking_mail.configuration()
    with booking.connection(path) as db:
        row = booking.get_row(db, ident)
        payload = booking.unpack(row)
        outbox = db.execute('SELECT state,attempts FROM booking_outbox WHERE request_id=?', (ident,)).fetchone()
        chat = db.execute('SELECT state,attempts,next_attempt FROM booking_chat WHERE request_id=?', (ident,)).fetchone()
        try:
            center = booking.resolve_center(db, payload['clinic_id'])
        except booking.BookingError:
            center = None
    secure = bool(chat) or booking_chat.enabled()
    recipient = payload['recipient']
    state = chat['state'] if chat else outbox['state'] if outbox else row['delivery']
    notes = []
    if center is None:
        notes.append('This center is not in the current published directory; the saved request recipient is retained.')
    elif recipient != booking.appointment_email(center):
        notes.append('The center email changed after submission. This request retains its original recipient.')
    if secure:
        if recipient.strip().casefold() == payload['patient_email'].strip().casefold():
            notes.append('Patient and center use the same email. Secure Mail may reject duplicate participants; test with separate inboxes.')
        if state == 'ready':
            notes.append('Secure Mail accepted the handoff. Check its notification outbox and recipient delivery logs; MySelfPay SMTP does not send these invitations.')
        elif not booking_chat.enabled():
            notes.append('This request has a Secure Mail queue entry, but the integration is currently disabled.')
        else:
            notes.append('Secure Mail handoff is pending. Check process_booking_chats and secure_mail_handoff warnings in the backend logs.')
    elif not config['booking_enabled']:
        notes.append('Direct booking email is disabled.')
    elif not booking_delivery.recipient_allowed(recipient):
        notes.append('The center recipient is excluded by the booking email allowlist.')
    elif state == 'sent':
        notes.append('SMTP accepted the email. Inbox delivery is not confirmed; inspect sender delivery/bounce logs and recipient spam.')
    elif state == 'uncertain':
        notes.append('SMTP acceptance is uncertain. Check provider logs before retrying to avoid duplicate messages.')
    elif state in ('queued', 'failed'):
        notes.append('Check the booking outbox worker and SMTP settings. This request has not been confirmed as accepted by SMTP.')
    else:
        notes.append('No queued direct email found. Suppressed historical requests are not automatically resent.')
    if row['link_expires'] <= time.time():
        notes.append('The center request link has expired.')
    return {'id': ident, 'center': payload['clinic_name'], 'recipient': recipient,
            'current_center_recipient': booking.appointment_email(center) if center else 'Not in current directory',
            'route': 'Secure Mail' if secure else 'Direct SMTP', 'state': state,
            'attempts': chat['attempts'] if chat else outbox['attempts'] if outbox else 0,
            'smtp_source': config['source'], 'notes': notes}
