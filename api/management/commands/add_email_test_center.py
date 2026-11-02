"""Publish a separate email-booking test center with no website or street directions."""
from .add_test_center import Command as BaseTestCenterCommand


class Command(BaseTestCenterCommand):
    help = 'Preview Gitesh Imaging Center in Dayton, OH; --publish adds this synthetic booking/email test center.'
    center_key = 'portal-gitesh-email-test-center'
    center_name = 'Gitesh Imaging Center'
    offer_prefix = 'portal-gitesh-email-test-'
    include_website = False
