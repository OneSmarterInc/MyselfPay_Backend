from functools import lru_cache
import re
import unicodedata

@lru_cache(maxsize=20000)
def normalized_name(value):
    return re.sub(r'[^a-z0-9]+',' ',unicodedata.normalize('NFKD',str(value)).encode('ascii','ignore').decode().lower()).strip()

def resolve_center(name, location, centers):
    candidates=[c for c in centers if normalized_name(c['name'])==normalized_name(name)]
    zip_code=re.search(r'\b\d{5}\b',location or '')
    if zip_code and len(candidates)>1:
        candidates=[c for c in candidates if zip_code[0] in c.get('location','')]
    return candidates[0]['key'] if len(candidates)==1 else ''
