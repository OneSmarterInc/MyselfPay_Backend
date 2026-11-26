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
