import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
import audit

YEARS = 7


class Command(BaseCommand):
    help = ('Check the audit chain and save a signed anchor. With AUDIT_ANCHOR_BUCKET set, the anchor goes to an S3 bucket '
            'that has Object Lock, where neither the app nor the server can change it. Run once a day.')

    def handle(self, *args, **options):
        if not audit.url():
            raise CommandError('AUDIT_DATABASE_URL is not set.')
        checked, bad, why = audit.verify_chain()
        body = audit.anchor()
        body.update(rows_checked=checked, chain_ok=bad is None, first_bad=bad, why=why)
        day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        name = f"anchor-{day}-{body['last_seq']}.json"
        folder = Path(os.environ.get('AUDIT_ANCHOR_DIR', 'audit-anchors'))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(json.dumps(body, indent=2, sort_keys=True))
        bucket = os.environ.get('AUDIT_ANCHOR_BUCKET', '').strip()
        if bucket:
            try:
                import boto3
            except ImportError as exc:
                raise CommandError('Install boto3 to upload anchors: pip install boto3') from exc
            until = datetime.now(timezone.utc) + timedelta(days=365 * YEARS)
            boto3.client('s3').put_object(Bucket=bucket, Key=f'audit-anchors/{name}', Body=json.dumps(body, sort_keys=True).encode(),
                                          ContentType='application/json', ObjectLockMode='COMPLIANCE', ObjectLockRetainUntilDate=until)
            self.stdout.write(f'Anchor {name} stored in s3://{bucket}/audit-anchors/ until {until:%Y-%m-%d}.')
        else:
            self.stdout.write(f'Anchor {name} saved in {folder}. Set AUDIT_ANCHOR_BUCKET to store it off the server.')
        if bad is not None:
            raise CommandError(f'CHAIN BROKEN at row {bad}: {why}')
