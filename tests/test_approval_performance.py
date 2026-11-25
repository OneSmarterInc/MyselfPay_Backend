"""Synthetic directory approval query budgets and incremental index correctness."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import core
import spatial
import center_profile
import center_price_apply


class ApprovalPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=str(Path(self.temp.name)/'approval.sqlite3')
        core.initialize(self.path)
        with core.connect(self.path) as db:
            spatial.initialize(db)
            for name in ('center-profile.sql','center-prices.sql'):db.executescript((core.ROOT/'ops'/name).read_text())
            db.execute("INSERT INTO batches VALUES('source','source','synthetic',0,'published','{}')")
            db.execute("INSERT INTO settings VALUES('active_batch','source')")
            self.entries={str(i):{'name':f'Center {i}','locations':[{'city':'Synthetic','state':'OH','latitude':39.1,'longitude':-84.5,'precision':'address'}]} for i in range(100)}
            db.executemany('INSERT INTO entries VALUES(?,?,?,?)',[('source',key,'hash',json.dumps(entry)) for key,entry in self.entries.items()])
            with patch('h3.latlng_to_cell',return_value='872a10000ffffff'):spatial.index_batch(db,'source')
    def destination(self,db,batch):
        db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)',(batch,batch,'synthetic',0,'staged','{}'))
        db.execute('INSERT INTO entries SELECT ?,entry_key,content_hash,payload FROM entries WHERE batch_id=?',(batch,'source'))
    def test_ready_index_copies_unchanged_records_without_h3_conversion(self):
        with core.connect(self.path) as db:
            self.destination(db,'copied');statements=[];db.set_trace_callback(statements.append)
            with patch('spatial.prepare_location',side_effect=AssertionError('Unchanged location recomputed')):
                spatial.clone_index(db,'source','copied')
            self.assertEqual(len(statements),4)
            source=[tuple(r) for r in db.execute('SELECT entry_key,ordinal,h3_res7,h3_res11,location FROM spatial_locations WHERE batch_id=? ORDER BY entry_key',('source',))]
            copied=[tuple(r) for r in db.execute('SELECT entry_key,ordinal,h3_res7,h3_res11,location FROM spatial_locations WHERE batch_id=? ORDER BY entry_key',('copied',))]
            self.assertEqual(source,copied)
            self.assertEqual(db.execute('SELECT COUNT(DISTINCT id) AS n FROM spatial_locations').fetchone()['n'],200)
    def test_edited_location_is_rebuilt_and_removed_coordinates_are_not_reused(self):
        with core.connect(self.path) as db:
            self.destination(db,'edited')
            db.execute('UPDATE entries SET payload=? WHERE batch_id=? AND entry_key=?',(json.dumps({'name':'Moved center','locations':[]}),'edited','0'))
            with patch('spatial.prepare_location',side_effect=AssertionError('Unchanged location recomputed')):
                spatial.clone_index(db,'source','edited',['0'])
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM spatial_locations WHERE batch_id=?',('edited',)).fetchone()['n'],99)
            self.assertIsNotNone(db.execute('SELECT value FROM settings WHERE key=?',('spatial-ready:edited',)).fetchone())
    def test_unready_source_falls_back_to_complete_index(self):
        with core.connect(self.path) as db:
            self.destination(db,'fallback');db.execute('DELETE FROM settings WHERE key=?',('spatial-ready:source',))
            with patch('h3.latlng_to_cell',return_value='872a10000ffffff'),patch('spatial.index_batch',wraps=spatial.index_batch) as rebuild:
                spatial.clone_index(db,'source','fallback')
            rebuild.assert_called_once_with(db,'fallback')
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM spatial_locations WHERE batch_id=?',('fallback',)).fetchone()['n'],100)
    def test_review_lists_load_names_in_one_join(self):
        with core.connect(self.path) as db:
            for i in range(100):
                db.execute('INSERT INTO center_profile_edits(id,entry_key,email_hash,field,value,submitted) VALUES(?,?,?,?,?,?)',(f'profile-{i}',str(i),'synthetic','address','10 Synthetic Street',i))
                db.execute('INSERT INTO center_price_submissions(id,entry_key,email_hash,exam,amount_cents,price_type,contrast,interpretation_included,submitted) VALUES(?,?,?,?,?,?,?,?,?)',(f'price-{i}',str(i),'synthetic','MRI',40000,'fixed','without','yes',i))
        for module in (center_profile,center_price_apply):
            connection=core.connect(self.path);statements=[];connection.set_trace_callback(statements.append)
            with patch('core.connect',return_value=connection):rows=module.pending(self.path)
            self.assertEqual(len(rows),100)
            self.assertTrue(all(row['center'].startswith('Center ') for row in rows))
            self.assertEqual(len([sql for sql in statements if sql.startswith('SELECT')]),2)
            self.assertTrue(all('entry_payload' not in row for row in rows))

    def test_failed_incremental_rebuild_rolls_back_publication(self):
        import center_contact
        with self.assertRaisesRegex(RuntimeError,'synthetic index failure'):
            with core.connect(self.path) as db,patch('spatial.index_entry',side_effect=RuntimeError('synthetic index failure')):
                center_contact._clone_batch(db,'synthetic-failure',{'0':self.entries['0']},{})
        with core.connect(self.path) as db:
            self.assertEqual(core.active_batch(db),'source')
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM batches').fetchone()['n'],1)
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM spatial_locations').fetchone()['n'],100)
