import logging
import psycopg
from rest_framework.response import Response
from rest_framework.views import APIView
import chr_index

log = logging.getLogger(__name__)


class CHRIndexView(APIView):
    def get(self, request):
        try:
            return Response(chr_index.compare(request.query_params))
        except ValueError as error:
            return Response({'error': str(error)}, status=400)
        except (psycopg.Error, RuntimeError):
            # Never expose connection strings, credentials or database exceptions.
            log.warning('CHR comparison database unavailable')
            return Response({'error': 'Hospital comparison is temporarily unavailable. Please try again.'}, status=503)

class CHRAvailabilityView(APIView):
    """Bounded, deduplicated checks using the same screening as the CHR dialog."""
    def post(self,request):
        import hashlib,json
        from django.core.cache import cache
        items=request.data.get('items') if isinstance(request.data,dict) else None
        allowed={'state','view','zip','city','latitude','longitude','mri','ct'}
        if not isinstance(items,list) or len(items)>24:
            return Response({'error':'Provide up to 24 comparison checks.'},status=400)
        results=[];seen={}
        for item in items:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str) or len(item['id'])>400 or not isinstance(item.get('query'),dict):
                return Response({'error':'Invalid comparison check.'},status=400)
            query={k:str(v) for k,v in item['query'].items() if k in allowed}
            if any(len(v)>160 for v in query.values()):return Response({'error':'Invalid comparison check.'},status=400)
            key='chr-available-v1:'+hashlib.sha256(json.dumps(query,sort_keys=True).encode()).hexdigest()
            if key not in seen:
                available=cache.get(key)
                if available is None:
                    try:
                        available=bool(chr_index.compare(query).get('hospital_count',0))
                        cache.set(key,available,60)
                    except ValueError:
                        available=False
                    except (psycopg.Error,RuntimeError):
                        # A transport/configuration failure is unknown, never proof of zero matches.
                        available=None
                seen[key]=available
            results.append({'id':item['id'],'available':seen[key]})
        return Response({'results':results})
