"""Visibility policy for excluded marketplace-linked directory records."""
import json


def blocked(payload):
    value = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return 'mdsave' in value.casefold().replace(' ', '').replace('-', '')


def visible_sql(column='payload'):
    if column not in ('payload', 'e.payload', 'o.payload'):
        raise ValueError('Unexpected directory payload column')
    return f"LOWER(REPLACE(REPLACE({column}, ' ', ''), '-', '')) NOT LIKE ?", ('%mdsave%',)
