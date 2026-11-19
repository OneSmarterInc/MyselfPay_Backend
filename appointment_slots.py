"""Center-confirmed appointment snapshots stored atomically with approval."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import booking


def selected(payload, choice, check_future=True):
    if not isinstance(choice, dict):
        raise booking.BookingError('Select a preferred time or enter an alternative date and time before approving.')
    zone = payload['timezone']
    if set(choice) == {'preferred_index'}:
        index = choice['preferred_index']
        if type(index) is not int or not 0 <= index < len(payload['slots']):
            raise booking.BookingError('Choose one of the patient’s preferred times.')
        slot = payload['slots'][index]
        result = {**slot, 'timezone':zone, 'source':'preferred'}
        raw_time = '08:00' if slot['period'] == 'morning' else '12:00'
    elif set(choice) == {'date', 'time'}:
        if not isinstance(choice['date'], str) or not isinstance(choice['time'], str):
            raise booking.BookingError('Enter a valid appointment date and time.')
        result = {'date':choice['date'], 'time':choice['time'], 'timezone':zone, 'source':'custom'}
        raw_time = choice['time']
    else:
        raise booking.BookingError('Choose a preferred time or provide an alternative date and time.')
    try:
        naive = datetime.strptime(result['date']+' '+raw_time, '%Y-%m-%d %H:%M')
        if naive.strftime('%Y-%m-%d %H:%M') != result['date']+' '+raw_time:
            raise ValueError()
    except (ValueError, TypeError):
        raise booking.BookingError('Enter a valid appointment date and time.') from None
    local = naive.replace(tzinfo=ZoneInfo(zone))
    if local.astimezone(timezone.utc).astimezone(ZoneInfo(zone)).replace(tzinfo=None) != naive:
        raise booking.BookingError('This local time does not exist because of a daylight-saving change. Choose another time.')
    if naive.replace(tzinfo=ZoneInfo(zone), fold=0).utcoffset() != naive.replace(tzinfo=ZoneInfo(zone), fold=1).utcoffset():
        raise booking.BookingError('This local time occurs twice during a daylight-saving change. Choose another time.')
    now = datetime.now(ZoneInfo(zone))
    if check_future and (local <= now or local.date() > now.date() + timedelta(days=90)):
        raise booking.BookingError('Choose a future appointment within the next 90 days.')
    return result


def update(con, row, status, choice=None):
    if status not in ('scheduled', 'voicemail', 'unable'):
        raise booking.BookingError('Choose a valid outcome.')
    payload = booking.unpack(row)
    if row['status'] in booking.TERMINAL:
        if row['status'] != status:
            raise booking.BookingError('This request is already closed.', 409)
        if status == 'scheduled' and choice is not None and selected(payload, choice, False) != payload.get('approved_slot'):
            raise booking.BookingError('This appointment was already approved. Its confirmed time cannot be replaced.', 409)
        return row
    if status == 'scheduled':
        payload['approved_slot'] = selected(payload, choice)
    import time
    closed = time.time() if status in booking.TERMINAL else None
    con.execute('UPDATE booking_requests SET status=?,closed=?,payload=? WHERE id=?',
                (status, closed, booking.pack(payload), row['id']))
    return booking.get_row(con, row['id'])


def label(slot):
    if not slot:
        return 'Approved time was not recorded. Contact the center for details.'
    day = datetime.strptime(slot['date'], '%Y-%m-%d').strftime('%a, %d %b %Y')
    when = booking.PERIODS[slot['period']] if slot.get('period') else datetime.strptime(slot['time'], '%H:%M').strftime('%I:%M %p').lstrip('0')
    return f"{day} · {when} · {booking.ZONES[slot['timezone']]} time"
