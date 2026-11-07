"""Publish an MDsave-free directory while retaining prior batches and requests."""
import json
import time
import uuid
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
import core
import spatial
from directory_policy import blocked, visible_sql


class Command(BaseCommand):
    help = 'Preview removal of MDsave-linked listings/offers; --publish creates a filtered active directory.'

    def add_arguments(self, parser):
        parser.add_argument('--publish', action='store_true')

    def handle(self, **options):
        removed_keys=[]
        with core.connect(settings.DOMAIN_DATABASE) as db:
            if options['publish']:
                db.execute("UPDATE settings SET value=value WHERE key='active_batch'")
            active=core.active_batch(db)
            if not active:
                raise CommandError('No published directory is active.')
            entries=db.execute('SELECT entry_key,payload FROM entries WHERE batch_id=?',(active,)).fetchall()
            offers=db.execute('SELECT entry_key,payload FROM offers WHERE batch_id=?',(active,)).fetchall()
            removed_keys=[r['entry_key'] for r in entries if blocked(r['payload'])]
            keys=set(removed_keys)
            removed_offers=sum(r['entry_key'] in keys or blocked(r['payload']) for r in offers)
            self.stdout.write(f'MDsave-linked listings: {len(keys)}; offers: {removed_offers}.')
            if not options['publish']:
                self.stdout.write('Preview only. Use --publish to apply. No records changed.')
                return
            if not keys and not removed_offers:
                self.stdout.write('No MDsave-linked directory records remain. No publication needed.')
                return
            batch=str(uuid.uuid4())
            stamp=time.time()
            report={'entry_count':len(entries)-len(keys),'offer_count':len(offers)-removed_offers,
                    'removed_mdsave_entries':len(keys),'removed_mdsave_offers':removed_offers,'parent_batch':active}
            db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)',(batch,core.digest({'parent':active,'policy':'exclude-mdsave','batch':batch}),'Directory without MDsave-linked records',stamp,'staged',json.dumps(report)))
            predicate, values=visible_sql()
            db.execute('INSERT INTO entries(batch_id,entry_key,content_hash,payload) SELECT ?,entry_key,content_hash,payload FROM entries WHERE batch_id=? AND '+predicate,(batch,active,*values))
            predicate, values=visible_sql('o.payload')
            db.execute('INSERT INTO offers(batch_id,id,entry_key,amount_cents,max_cents,price_type,payload) SELECT ?,o.id,o.entry_key,o.amount_cents,o.max_cents,o.price_type,o.payload FROM offers o JOIN entries e ON e.batch_id=? AND e.entry_key=o.entry_key WHERE o.batch_id=? AND '+predicate,(batch,batch,active,*values))
            spatial.index_batch(db,batch)
            db.execute("UPDATE batches SET status='published' WHERE id=?",(batch,))
            db.execute("UPDATE settings SET value=? WHERE key='active_batch'",(batch,))
            db.execute('INSERT INTO publish_events(batch_id,previous_batch,created,action) VALUES(?,?,?,?)',(batch,active,stamp,'remove_mdsave'))
        from api.models import MailerContact, MailerMessage
        if removed_keys:
            MailerContact.objects.filter(center_key__in=removed_keys).update(active=False)
            MailerMessage.objects.filter(contact__center_key__in=removed_keys,state__in=['draft','queued']).update(state='cancelled',error='MDsave-linked listing removed.')
        from directory_cache import clear
        clear()
        self.stdout.write(self.style.SUCCESS(f'Published {batch}. Prior batch {active} and all appointment history preserved.'))
