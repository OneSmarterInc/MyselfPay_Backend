"""Keep marketplace-linked listings visible; branding is presentation-only."""
import json


def mdsave_linked(payload):
    value = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return 'mdsave' in value.casefold().replace(' ', '').replace('-', '')


def blocked(payload):
    # Retained for callers introduced by the previous exclusion policy.
    return False


def visible_sql(column='payload'):
    if column not in ('payload', 'e.payload', 'o.payload'):
        raise ValueError('Unexpected directory payload column')
    return '1=1', ()
