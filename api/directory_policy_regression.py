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
            for ident,key,payload in [('direct-offer','direct',{'id':'direct-offer','mod':'MRI','type':'fixed'}),('blocked-offer','direct',{'id':'blocked-offer','source':'https://mdsave.com/package'}),('blocked-entry-offer','mdsave',{'id':'blocked-entry-offer','mod':'MRI'})]:
                db.execute('INSERT INTO offers VALUES(?,?,?,?,?,?,?)',('active',ident,key,40000,None,'fixed',json.dumps(payload)))
            # Simulate a previously indexed linked entry. The bundle must also filter stale rows.
            for row in db.execute('SELECT entry_key,payload FROM entries WHERE batch_id=?',('active',)).fetchall():
                spatial.index_entry(db,'active',row['entry_key'],self.site)
            db.execute("INSERT INTO settings VALUES('spatial-ready:active','1')")

    def test_catalog_metadata_spatial_and_center_portal_exclude_linked_entries(self):
        catalog=core.catalog(self.path)
        self.assertEqual({e['booking_id'] for e in catalog['entries']},{'direct','maryland'})
        self.assertEqual([o['id'] for o in catalog['offers']],['direct-offer'])
        metadata=core.catalog_metadata(self.path)
        self.assertEqual(metadata['entry_count'],2);self.assertEqual(metadata['offer_count'],1)
        records=spatial.bundle(self.path,h3.latlng_to_cell(39.1,-84.5,7))
        self.assertEqual(records['count'],2)
        self.assertEqual({r['entry_key'] for r in records['records']},{'direct','maryland'})
        self.assertFalse(blocked(records))
        with core.connect(self.path) as db:
            self.assertEqual({e['id'] for e in center_portal.centers(db,'center@example.test')},{'direct','maryland'})
            with self.assertRaises(booking.BookingError):booking.resolve_center(db,'mdsave')
            with self.assertRaises(booking.BookingError):booking.price_snapshot(db,'direct','blocked-offer')
            self.assertEqual(spatial.index_entry(db,'active','mdsave',{'name':'MD-save offer'}),0)

    def test_publication_removes_records_preserves_old_batch_and_stops_pending_mail(self):
        contact=MailerContact.objects.create(center_key='mdsave',email='linked@example.test',centers=['Fleet Street'],imported_at=__import__('django.utils.timezone',fromlist=['now']).now())
        campaign=MailerCampaign.objects.create(subject='Synthetic',body='Synthetic',selection='1',reply_to='reply@example.test',created_by='test')
        message=MailerMessage.objects.create(campaign=campaign,contact=contact,email=contact.email,center_names='Fleet Street',position=1,message_id='<policy@example.test>',state='queued')
        call_command('remove_mdsave',stdout=StringIO())
        with core.connect(self.path) as db:self.assertEqual(core.active_batch(db),'active')
        call_command('remove_mdsave',publish=True,stdout=StringIO())
        with core.connect(self.path) as db:
            active=core.active_batch(db);self.assertNotEqual(active,'active')
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM entries WHERE batch_id=?',(active,)).fetchone()['n'],2)
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM offers WHERE batch_id=?',(active,)).fetchone()['n'],1)
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM entries WHERE batch_id=?',('active',)).fetchone()['n'],4)
        message.refresh_from_db();contact.refresh_from_db()
        self.assertEqual(message.state,'cancelled');self.assertFalse(contact.active)
        call_command('remove_mdsave',publish=True,stdout=StringIO())
        with core.connect(self.path) as db:self.assertEqual(core.active_batch(db),active)
        core.publish('active',self.path)
        self.assertEqual(core.catalog_metadata(self.path)['entry_count'],2)
