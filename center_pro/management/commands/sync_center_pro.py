from django.core.management.base import BaseCommand
from center_pro.integration import sync_directory,sync_requests
from center_pro.models import Center
class Command(BaseCommand):
    help='Map the existing directory and requests into Center Pro; never seed users or assignments.'
    def handle(self,*args,**options):
        count=sync_directory()
        for center in Center.objects.filter(directory_active=True):sync_requests(center)
        self.stdout.write(self.style.SUCCESS(f'Mapped {count} directory centers. Existing accounts and roles preserved.'))
