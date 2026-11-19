"""Merge records from the previous MDsave cleanup without replacing newer data."""
import json
import time
import uuid
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
import core
import spatial
from directory_policy import mdsave_linked


class Command(BaseCommand):
    help = 'Preview restoration of MDsave-linked centers/offers removed by the old cleanup; --publish applies it.'

    def add_arguments(self, parser):
        parser.add_argument('--publish', action='store_true')

    def handle(self, **options):
        with core.connect(settings.DOMAIN_DATABASE) as db:
            if options['publish']:
                db.execute("UPDATE settings SET value=value WHERE key='active_batch'")
            active=core.active_batch(db)
            if not active: raise CommandError('No active directory.')
            event=db.execute("SELECT previous_batch FROM publish_events WHERE action='remove_mdsave' ORDER BY created DESC,id DESC LIMIT 1").fetchone()
            if not event:
                self.stdout.write('No previous MDsave removal found. Existing centers are already allowed.')
                return
            source=event['previous_batch']
            previous={row['entry_key']:dict(row) for row in db.execute('SELECT * FROM entries WHERE batch_id=?',(source,))}
            current={row['entry_key']:dict(row) for row in db.execute('SELECT * FROM entries WHERE batch_id=?',(active,))}
            entries=[row for key,row in previous.items() if key not in current and mdsave_linked(row['payload'])]
            merged={**current,**{row['entry_key']:row for row in entries}}
            existing={row['id'] for row in db.execute('SELECT id FROM offers WHERE batch_id=?',(active,))}
            offers=[]
            for row in db.execute('SELECT * FROM offers WHERE batch_id=?',(source,)):
                key=row['entry_key']
                if row['id'] in existing or key not in merged or key not in previous: continue
                if merged[key]['content_hash'] != previous[key]['content_hash']: continue
                if mdsave_linked(row['payload']) or mdsave_linked(previous[key]['payload']): offers.append(dict(row))
            self.stdout.write(f'Restore {len(entries)} centers and {len(offers)} offers. Newer records will not be overwritten.')
            if not options['publish'] or not (entries or offers): return
            batch=str(uuid.uuid4());stamp=time.time()
            report={'parent_batch':active,'restored_from':source,'restored_entries':len(entries),'restored_offers':len(offers)}
            db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)',(batch,core.digest({'restore':source,'batch':batch}),'Restore directory centers; hide marketplace branding only',stamp,'staged',json.dumps(report)))
            db.execute('INSERT INTO entries SELECT ?,entry_key,content_hash,payload FROM entries WHERE batch_id=?',(batch,active))
            db.execute('INSERT INTO offers SELECT ?,id,entry_key,amount_cents,max_cents,price_type,payload FROM offers WHERE batch_id=?',(batch,active))
            for row in entries: db.execute('INSERT INTO entries VALUES(?,?,?,?)',(batch,row['entry_key'],row['content_hash'],row['payload']))
            for row in offers: db.execute('INSERT INTO offers VALUES(?,?,?,?,?,?,?)',(batch,row['id'],row['entry_key'],row['amount_cents'],row['max_cents'],row['price_type'],row['payload']))
            spatial.index_batch(db,batch)
            report.update(entry_count=len(merged),offer_count=len(existing)+len(offers))
            db.execute("UPDATE batches SET status='published',report=? WHERE id=?",(json.dumps(report),batch))
            db.execute("UPDATE settings SET value=? WHERE key='active_batch'",(batch,))
            db.execute('INSERT INTO publish_events(batch_id,previous_batch,created,action) VALUES(?,?,?,?)',(batch,active,stamp,'restore_directory'))
        from directory_cache import clear
        clear()
        self.stdout.write(self.style.SUCCESS('Centers restored. Existing appointment history and stopped mail campaigns are unchanged.'))
