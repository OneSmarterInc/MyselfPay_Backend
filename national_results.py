"""Include nationwide directory leads even when no price offer was imported."""
import re
import core


def include_national_directory(result, path):
    search = result['search']
    if search['location'].strip().casefold() != 'national':
        return result
    result['location_notice'] = 'Showing indexed imaging centers across all states. Exam and price filters still apply; confirm availability and prices directly.'
    result['results_heading'] = 'Imaging centers across all states'
    result['program_heading'] = 'National and regional programs'
    if search['type'] not in ('all', 'quote'):
        return result
    represented = {offer['entry']['name'] for offer in result['offers'] + result['programs']}
    facilities, programs = [], []
    for entry in core.catalog(path)['entries']:
        if entry['name'] in represented:
            continue
        wording = str(entry.get(search['mod'].lower(), '')).strip() if search['mod'] else 'Confirm service availability'
        if not wording or wording.casefold() in ('n/a', 'none', 'no'):
            continue
        if re.search(r'\b(?:not (?:offered|listed|verified|available)|unavailable)\b', wording, re.I):
            continue
        (programs if entry.get('location_type') == 'program' else facilities).append(entry)
    order = lambda entry: (str(entry.get('states', '')).casefold(), entry['name'].casefold())
    result['nearby_unpriced'] = sorted(facilities, key=order)
    result['program_entries'] = sorted(programs, key=order)
    return result
