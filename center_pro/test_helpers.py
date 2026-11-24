from django.conf import settings
from rest_framework.test import APIClient as BaseClient
class OriginClient(BaseClient):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.defaults['HTTP_ORIGIN']=settings.FRONTEND_ORIGIN

def grant_admin_mfa(client,user,enabled=True):
    from api.models import AdminTOTP
    from admin_settings import secret_cipher
    profile,_=AdminTOTP.objects.get_or_create(user=user,defaults={'secret_encrypted':secret_cipher().encrypt(b'JBSWY3DPEHPK3PXP').decode(),'enabled':True})
    session=client.session
    session['admin_totp_verified']=str(profile.generation) if enabled else ''
    session.save()
