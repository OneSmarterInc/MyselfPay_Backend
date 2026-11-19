from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Retired: MDsave branding is hidden while centers and prices remain visible.'

    def add_arguments(self, parser):
        parser.add_argument('--publish', action='store_true')

    def handle(self, **options):
        raise CommandError('Listing removal is retired. Use restore_mdsave --publish to restore previously removed centers. Branding is hidden by the frontend.')
