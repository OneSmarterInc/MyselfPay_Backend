from django.core.management.base import BaseCommand, CommandError

class Command(BaseCommand):
    help='Read Secure Mail public API readiness without sending messages.'
    def handle(self,*args,**options):
        from securemail_api import request
        try:result=request('/status')
        except Exception:raise CommandError('Secure Mail status check failed. Check API URL, key permissions and server connectivity; no email was sent.') from None
        if not result.get('ready'):raise CommandError('Secure Mail authenticated but is not ready; no email was sent.')
        self.stdout.write(self.style.SUCCESS('Secure Mail API ready. No email sent.'))
