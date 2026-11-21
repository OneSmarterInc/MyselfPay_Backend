from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from api.models import AdminTOTP


class Command(BaseCommand):
    help = 'Server-operator recovery: remove one administrator authenticator and revoke its verified sessions.'

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--confirm', action='store_true')

    def handle(self, username, confirm, **options):
        user = get_user_model().objects.filter(username=username, is_superuser=True).first()
        if not user:
            raise CommandError('Administrator not found.')
        if not confirm:
            self.stdout.write('No change made. Add --confirm to reset this administrator authenticator.')
            return
        AdminTOTP.objects.filter(user=user).delete()
        self.stdout.write('Authenticator reset. Existing TOTP proofs are revoked. Sign in with the password and enroll again.')
