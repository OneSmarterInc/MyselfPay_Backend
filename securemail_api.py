"""Server-only Secure Mail v1 adapter. Never logs credentials or message content."""
import json
import os
import ssl
import hashlib
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise ValueError('Secure Mail redirects are not allowed')

def request(path, data=None, idempotency_key=None):
    base=os.getenv('SECUREMAIL_BASE_URL','https://securemailbackend.onesmarter.com/api/v1').rstrip('/')
    url=urlsplit(base)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path!='/api/v1':
        raise ValueError('SECUREMAIL_BASE_URL must be an HTTPS /api/v1 URL')
    key=os.environ['SECUREMAIL_API_KEY'].strip()
    if not key or any(c in key for c in '\r\n'):raise ValueError('Secure Mail API key is missing or invalid')
    headers={'Authorization':'Bearer '+key,'Accept':'application/json'}
    raw=None
    if data is not None:
        if not idempotency_key or len(idempotency_key)>100:raise ValueError('A stable idempotency key is required')
        headers.update({'Content-Type':'application/json','Idempotency-Key':idempotency_key})
        raw=json.dumps(data,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    context=ssl.create_default_context()
    opener=build_opener(NoRedirect(),HTTPSHandler(context=context))
    with opener.open(Request(base+path,data=raw,headers=headers),timeout=15) as response:
        result=json.loads(response.read(1000001))
    if not isinstance(result,dict):raise ValueError('Invalid Secure Mail response')
    return result

def send_message(message, recipient):
    part=message.get_body(preferencelist=('plain',)) if message.is_multipart() else message
    body=part.get_content() if part else ''
    data={'sender':os.environ['SECUREMAIL_SENDER'],'to':recipient,'subject':str(message.get('Subject','')),'body':str(body),'reply':False}
    if not data['body'] or len(data['body'])>100000 or not data['subject'] or len(data['subject'])>180:
        raise ValueError('Secure Mail message exceeds supported fields')
    # An existing Message-ID is stable across retries; content hash also supports callers without one.
    canonical=json.dumps(data,sort_keys=True,separators=(',',':')).encode()
    ident=hashlib.sha256(canonical).hexdigest()
    result=request('/messages',data,'selfpay-message-'+ident)
    if not result.get('id'):raise ValueError('Secure Mail did not return a message ID')
    return result
