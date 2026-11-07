import json
import tempfile
from pathlib import Path
from io import StringIO
from django.test import TestCase, override_settings
from django.core.management import call_command
import h3
import core
import spatial
import booking
import center_portal
from directory_cache import clear
from directory_policy import blocked
from api.models import MailerContact, MailerCampaign, MailerMessage


class DirectoryPolicyTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=str(Path(self.temp.name)/'policy.sqlite3')
        self.config=override_settings(DOMAIN_DATABASE=self.path)
        self.config.enable();self.addCleanup(self.config.disable)
        clear();core.initialize(self.path)
        self.site={'name':'Independent center','states':'OH','area':'Cincinnati','location_type':'site',
            'locations':[{'city':'Cincinnati','state':'OH','latitude':39.1,'longitude':-84.5,'precision':'address'}],
            'email':'center@example.test','source':'https://example.test','mri':'$400'}
        with core.connect(self.path) as db:
            db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)',('active','active','Synthetic',0,'published','{}'))
            db.execute("INSERT INTO settings VALUES('active_batch','active')")
            for key,entry in [('direct',self.site),('mdsave',{**self.site,'name':'Fleet Street','source':'https://www.mdsave.com/p/example'}),('spaced',{**self.site,'name':'MD Save offer'}),('maryland',{**self.site,'name':'Maryland center','states':'MD','area':'Baltimore'})]:
                db.execute('INSERT INTO entries VALUES(?,?,?,?)',('active',key,key,json.dumps(entry)))
            for ident,key,payload in [('direct-offer','direct',{'id':'direct-offer','mod':'MRI','type':'fixed'}),('blocked-offer','direct',{'id':'blocked-offer','type':'fixed','source':'https://mdsave.com/package'}),('blocked-entry-offer','mdsave',{'id':'blocked-entry-offer','mod':'MRI'})]:
                db.execute('INSERT INTO offers VALUES(?,?,?,?,?,?,?)',('active',ident,key,40000,None,'fixed',json.dumps(payload)))
            # Simulate a previously indexed linked entry. The bundle must also filter stale rows.
            for row in db.execute('SELECT entry_key,payload FROM entries WHERE batch_id=?',('active',)).fetchall():
                spatial.index_entry(db,'active',row['entry_key'],self.site)
            db.execute("INSERT INTO settings VALUES('spatial-ready:active','1')")

    def test_all_marketplace_centers_and_prices_remain_available(self):
        catalog=core.catalog(self.path)
        self.assertEqual({e['booking_id'] for e in catalog['entries']},{'direct','maryland','mdsave','spaced'})
        self.assertEqual(len(catalog['offers']),3)
        metadata=core.catalog_metadata(self.path)
        self.assertEqual(metadata['entry_count'],4);self.assertEqual(metadata['offer_count'],3)
        records=spatial.bundle(self.path,h3.latlng_to_cell(39.1,-84.5,7))
        self.assertEqual(records['count'],4)
        with core.connect(self.path) as db:
            self.assertEqual(len(center_portal.centers(db,'center@example.test')),4)
            self.assertEqual(booking.resolve_center(db,'mdsave')['name'],'Fleet Street')
            self.assertIn('$400',booking.price_snapshot(db,'direct','blocked-offer'))

    def test_restoration_merges_removed_centers_without_overwriting_newer_data(self):
        with core.connect(self.path) as db:
            db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)',('clean','clean','Synthetic cleanup',1,'published','{}'))
            db.execute("INSERT INTO entries SELECT 'clean',entry_key,content_hash,payload FROM entries WHERE batch_id='active' AND entry_key IN ('direct','maryland')")
            db.execute("INSERT INTO offers SELECT 'clean',id,entry_key,amount_cents,max_cents,price_type,payload FROM offers WHERE batch_id='active' AND id='direct-offer'")
            db.execute("UPDATE entries SET content_hash='newer',payload=? WHERE batch_id='clean' AND entry_key='direct'",(json.dumps({**self.site,'name':'Updated independent center'}),))
            db.execute("INSERT INTO entries VALUES('clean','new-center','new-center',?)",(json.dumps({**self.site,'name':'Newly added center'}),))
            db.execute("UPDATE settings SET value='clean' WHERE key='active_batch'")
            db.execute("INSERT INTO publish_events(batch_id,previous_batch,created,action) VALUES('clean','active',1,'remove_mdsave')")
        call_command('restore_mdsave',stdout=StringIO())
        with core.connect(self.path) as db:self.assertEqual(core.active_batch(db),'clean')
        call_command('restore_mdsave',publish=True,stdout=StringIO())
        with core.connect(self.path) as db:
            active=core.active_batch(db);self.assertNotEqual(active,'clean')
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM entries WHERE batch_id=?',(active,)).fetchone()['n'],5)
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM offers WHERE batch_id=?',(active,)).fetchone()['n'],2)
            self.assertEqual(booking.resolve_center(db,'direct')['name'],'Updated independent center')
            self.assertEqual(booking.resolve_center(db,'mdsave')['name'],'Fleet Street')
        call_command('restore_mdsave',publish=True,stdout=StringIO())
        with core.connect(self.path) as db:self.assertEqual(core.active_batch(db),active)

    def test_old_removal_command_is_retired(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):call_command('remove_mdsave',publish=True,stdout=StringIO())
        with core.connect(self.path) as db:self.assertEqual(core.active_batch(db),'active')
