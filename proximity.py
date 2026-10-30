"""Pure distance/grouping helpers: consumes imported coordinates, never geocodes."""
import math
import re


def miles(a,b):
    lat1,lat2=map(math.radians,[a['latitude'],b['latitude']])
    dlat=lat2-lat1;dlon=math.radians(b['longitude']-a['longitude'])
    h=math.sin(dlat/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 3958.7613*2*math.asin(math.sqrt(min(1,max(0,h))))


def nearest(origin,locations):
    valid=[]
    for loc in locations:
        if not isinstance(loc,dict):continue
        if loc.get('precision') not in ('address','zip','city'):continue
        if any(isinstance(loc.get(k),bool) or not isinstance(loc.get(k),(int,float)) or not math.isfinite(loc[k]) or abs(loc[k])>limit for k,limit in [('latitude',90),('longitude',180)]):continue
        valid.append((miles(origin,loc),loc))
    return min(valid,key=lambda x:x[0]) if valid else None


def covers(entry,state):
    explicit_nationwide=bool(re.search(r'\b(?:nationwide|all states)\b',str(entry.get('coverage','')),re.I))
    return explicit_nationwide or state in [s.strip().upper() for s in re.split('[;,/]',entry.get('states',''))] or entry.get('states','').strip().casefold() in ('national','nationwide','all states','us','usa')


def offer_locations(offer):
    entry=offer['entry'];locations=entry.get('locations',[])
    # A multi-site operator is not evidence that a price applies to every site.
    # Match named cities in the mapping's existing region, or require explicit
    # reviewed offer-level locations for broader mappings.
    if 'locations' in offer:return offer['locations']
    if len(locations)<=1:return locations
    region=offer.get('region','').casefold()
    return [loc for loc in locations if loc.get('city') and re.search(r'(?<!\w)'+re.escape(loc['city'].casefold())+r'(?!\w)',region)]


def annotate(item,match):
    distance,loc=match
    return dict(item,distance_miles=round(distance,1),distance_precision=loc['precision'],nearest_location=loc,_distance=distance)


