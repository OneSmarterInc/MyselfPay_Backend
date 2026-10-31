"""Apply optional filters independently, without relaxing the user's choices."""
import re
import core
from proximity import annotate, covers, nearest, offer_locations


def supports(entry, service):
    if not service:
        return True
    text = str(entry.get(service.lower(), '')).strip()
    return bool(text and text.casefold() not in ('no', 'none', 'n/a') and not re.search(
        r'\b(?:not (?:offered|listed|verified|available)|unavailable)\b', text, re.I))


def has_words(text, phrase):
    return bool(re.search(r'(?<!\w)' + re.escape(phrase.strip()) + r'(?!\w)', text, re.I))


def directory_matches(entry, search):
    if search['type'] not in ('all', 'quote') or search['budget'] or not supports(entry, search['mod']):
        return False
    text = ' '.join(str(entry.get(key, '')) for key in (
        (search['mod'].lower(), 'notes') if search['mod'] else ('mri', 'ct', 'other', 'notes')))
    if search['body'].strip() and not has_words(text, search['body']):
        return False
    contrast = search['contrast']
    if contrast != 'unknown':
        both = bool(re.search(r'\b(?:without and with|with and without)\s+contrast\b', text, re.I))
        if contrast == 'both':
            return both
        if both:
            return False
        return bool(re.search(r'\b' + ('without' if contrast == 'without' else 'with') + r'\s+contrast\b', text, re.I))
    return True


def apply_filters(result, path, requested_sort=None, requested_scope=None, *, informational_body=False, statewide=False):
    search = result['search']
    if requested_sort is not None:
        search['sort'] = requested_sort
    if informational_body:
        search = {**search, 'body': ''}
    national = search['location'].strip().casefold() == 'national'
    origin = result.get('resolved_location')
    city_only = bool(requested_scope == 'city' and origin and origin['kind'] == 'place')
    if requested_scope == 'city':
        search['scope'] = 'city' if city_only else 'state'
        result['search']['scope'] = search['scope']
    if not national and (not origin or result.get('location_suggestions')):
        return result
    state = origin['state'] if origin else None
    spatial = bool(not statewide and origin and origin['kind'] in ('place', 'zcta') and search['scope'] == 'radius')
    data = core.catalog(path)
    offers, programs, facilities, program_entries = [], [], [], []
    missing_coordinates = 0

    def located(entry, locations):
        nonlocal missing_coordinates
        is_program = entry.get('location_type') == 'program'
        if national:
            return dict(entry)
        if city_only:
            city = origin.get('name', search['location'].split(',')[0])
            city_locations = [loc for loc in locations if loc.get('state') == state and str(loc.get('city', '')).casefold() == city.casefold()]
            area_matches = has_words(str(entry.get('area', '')), city) and (covers(entry, state) or any(loc.get('state') == state for loc in entry.get('locations', [])))
            if not city_locations and not area_matches:
                return None
            match = nearest(origin, city_locations)
            return annotate(entry, match) if match else dict(entry)
        if is_program:
            return dict(entry) if covers(entry, state) else None
        if spatial:
            match = nearest(origin, locations)
            if not match:
                missing_coordinates += 1
                return None
            return annotate(entry, match) if match[0] <= search['radius'] else None
        state_locations = [loc for loc in locations if loc.get('state') == state]
        if not (covers(entry, state) or any(loc.get('state') == state for loc in entry.get('locations', []))):
            return None
        match = nearest(origin, state_locations) if origin['kind'] != 'state' else None
        return annotate(entry, match) if match else dict(entry)

    for original in data['offers']:
        if search['mod'] and original['mod'] != search['mod']:
            continue
        if search['contrast'] != 'unknown' and original['contrast'] != search['contrast']:
            continue
        terms = original.get('exam_terms')
        if search['body'].strip() and terms and not any(has_words(search['body'], term) for term in terms):
            continue
        if search['type'] != 'all' and original['type'] != search['type']:
            continue
        amount = original.get('amount')
        if search['budget'] and (amount is None or original['type'] in ('conflict', 'quote') or amount > search['budget']):
            continue
        locations = offer_locations(original)
        if not national and not spatial and original['entry'].get('location_type') != 'program' and locations and not any(loc.get('state') == state for loc in locations):
            continue
        entry = located(original['entry'], locations)
        if entry is None:
            continue
        offer = dict(original)
        for key in ('distance_miles', 'distance_precision', 'nearest_location', '_distance'):
            if key in entry:
                offer[key] = entry[key]
        offer['match_status'] = 'Exact body-part and protocol applicability requires confirmation'
        offer['budget_status'] = ('No budget set' if not search['budget'] else
            'Published amount within budget · exact scope needs confirmation' if original['type'] == 'fixed' and original['contrast'] != 'unknown' else 'Budget fit unconfirmed')
        (programs if original['entry'].get('location_type') == 'program' else offers).append(offer)
    represented = {offer['entry'].get('booking_id', offer['entry']['name']) for offer in offers + programs}
    for original in data['entries']:
        if original.get('booking_id', original['name']) in represented or not directory_matches(original, search):
            continue
        entry = located(original, original.get('locations', []))
        if entry is not None:
            (program_entries if entry.get('location_type') == 'program' else facilities).append(entry)
    if search['sort'] == 'price':
        offers.sort(key=lambda offer: offer['amount'] if offer['type'] == 'fixed' and offer.get('amount') is not None else float('inf'))
        programs.sort(key=lambda offer: offer['amount'] if offer['type'] == 'fixed' and offer.get('amount') is not None else float('inf'))
    elif search['sort'] == 'distance':
        offers.sort(key=lambda offer: offer.get('_distance', float('inf')))
        programs.sort(key=lambda offer: offer.get('_distance', float('inf')))
    facilities.sort(key=lambda entry: (entry.get('_distance', float('inf')), str(entry.get('states', '')), entry['name']))
    program_entries.sort(key=lambda entry: entry['name'])
    notice = ('Showing indexed imaging centers across all states. Exam and price filters still apply; confirm availability and prices directly.' if national else
        'Distances are approximate straight-line miles from the searched location, not driving distances.' if spatial else
        'Showing indexed imaging centers across the selected state. Optional filters apply.')
    if search['budget']:
        notice += ' Listings without a published numeric price are excluded by your budget. Starting prices, ranges and averages still require confirmation.'
    if search['body'].strip() or search['contrast'] != 'unknown':
        notice += ' Directory-only centers need matching exam or contrast information to appear; unverified matches are excluded.'
    result.update(offers=offers, count=len(offers), programs=programs, nearby_unpriced=facilities,
        program_entries=program_entries, fallback=False, no_exam_price=False,
        unlocated_offer_count=missing_coordinates, location_notice=notice,
        results_heading='Imaging centers across all states' if national else '',
        program_heading='National and regional programs' if national else 'Programs available in ' + state)
    return result
