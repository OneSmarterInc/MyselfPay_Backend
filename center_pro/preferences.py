from datetime import datetime, time
from zoneinfo import ZoneInfo
from rest_framework.exceptions import ValidationError
from .models import WorkingHours

PERIODS = {'morning': (time(8), time(12)), 'afternoon': (time(12), time(17))}
def preference_bounds(center, preference):
    if not isinstance(preference, dict) or preference.get('period') not in PERIODS:
        raise ValidationError('Choose Morning or Afternoon.')
    try:
        day=datetime.strptime(preference['date'], '%Y-%m-%d').date()
        if preference['date'] != day.isoformat():
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise ValidationError('Enter a valid preference date.')
    hours=WorkingHours.objects.filter(center=center,weekday=day.weekday()).first()
    if not hours or hours.closed:
        raise ValidationError('The center is closed or working hours are unavailable on this date.')
    begin,end=PERIODS[preference['period']]
    begin=max(begin,hours.opens);end=min(end,hours.closes)
    if begin>=end:
        raise ValidationError('This period is outside the center working hours.')
    zone=ZoneInfo(center.timezone)
    return datetime.combine(day,begin,zone),datetime.combine(day,end,zone)
