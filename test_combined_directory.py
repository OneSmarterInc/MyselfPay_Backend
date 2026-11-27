import csv
import json
import tempfile
import unittest
from pathlib import Path
import core
from combined_directory import normalize, prepare, read_csv, upsert
from proximity import covers

class CombinedDirectoryTests(unittest.TestCase):
    def raw(self, ident='D-1', **kw):
        return {'A':'Example Imaging','B':'Dayton','C':'OH','F':'$499 / $599 — UNVERIFIED source prices\nWithout contrast $499; with contrast $599\n\nSUPPLIED FINDINGS — preserved evidence','G':'$200','K':'https://example.com/prices','M':'2026-11-27','X':'Main','Y':ident,'BA':'45402',**kw}
    def write_csv(self,path,raws):
        rows=[]
        for raw in raws:
            e=normalize(raw)
            row={k:v for k,v in e.items() if not isinstance(v,(dict,list))}
            row.update(import_action='upsert',source_json=core.canonical(raw),locations_json=core.canonical(e['locations']))
            rows.append(row)
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=sorted(set().union(*(r.keys() for r in rows))));writer.writeheader();writer.writerows(rows)
    def test_zip_state_pairing_and_source_prices(self):
        e=normalize(self.raw(C='AL; GA; SC',BA='29169; 30092; 35242'))
        self.assertEqual({(l['state'],l['zip']) for l in e['locations']},{('SC','29169'),('GA','30092'),('AL','35242')})
        self.assertTrue(all(covers(e,s) for s in ['AL','GA','SC']))
        self.assertEqual(e['zip'],'')
        self.assertIn('with contrast $599',e['mri']);self.assertNotIn('SUPPLIED FINDINGS',e['mri'])
        self.assertIn('SUPPLIED FINDINGS',e['source_fields']['F']);self.assertEqual(e['price_verification'],'unverified')
        e=normalize(self.raw(C='MA',BA='01002'));self.assertEqual(e['zip'],'01002')
    def test_atomic_upsert_idempotence_and_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            dbfile=Path(folder)/'test.sqlite';csvfile=Path(folder)/'input.csv';core.initialize(dbfile)
            original=normalize(self.raw());other=normalize(self.raw('keep',A='Keep Me'))
            with core.connect(dbfile) as db:
                db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)',('old','hash','test',1,'published','{}'))
                db.execute("INSERT INTO settings VALUES('active_batch','old')")
                for key,e in [('stable-booking-id',original),('keep',other)]:db.execute('INSERT INTO entries VALUES(?,?,?,?)',('old',key,core.digest(e),core.canonical(e)))
                db.execute('INSERT INTO offers VALUES(?,?,?,?,?,?,?)',('old','offer','stable-booking-id',49900,None,'fixed','{}'))
            self.write_csv(csvfile,[self.raw(A='Renamed Imaging',BA='45123'),self.raw('new',A='New Center',C='MA',BA='01002',X='Additional candidate')])
            result=upsert(csvfile,dbfile,True)
            self.assertEqual(result['new_centers'],1);self.assertEqual(result['preserved_unmatched_centers'],1)
            with core.connect(dbfile) as db:self.assertEqual(core.active_batch(db),'old')
            from unittest.mock import patch
            with patch('spatial.index_batch',side_effect=RuntimeError('index failed')):
                with self.assertRaisesRegex(RuntimeError,'index failed'):upsert(csvfile,dbfile,False)
            with core.connect(dbfile) as db:
                self.assertEqual(core.active_batch(db),'old')
                self.assertEqual(db.execute('SELECT COUNT(*) n FROM batches').fetchone()['n'],1)
            result=upsert(csvfile,dbfile,False);batch=result['batch']
            with core.connect(dbfile) as db:
                self.assertEqual(core.active_batch(db),batch)
                e=json.loads(db.execute('SELECT payload FROM entries WHERE batch_id=? AND entry_key=?',(batch,'stable-booking-id')).fetchone()['payload'])
                self.assertEqual(e['name'],'Renamed Imaging');self.assertEqual(e['zip'],'45123')
                self.assertEqual(db.execute('SELECT COUNT(*) n FROM offers WHERE batch_id=?',(batch,)).fetchone()['n'],0)
                self.assertEqual(db.execute('SELECT COUNT(*) n FROM entries WHERE batch_id=?',('old',)).fetchone()['n'],2)
            self.assertEqual(upsert(csvfile,dbfile,False)['changed_entries'],0)
            self.assertNotIn('source_fields',core.catalog(dbfile)['entries'][0])
            preview=upsert(csvfile,dbfile,True,replace_existing=True)
            self.assertEqual(preview['removed_unmatched_centers'],1);self.assertEqual(preview['changed_entries'],0)
            self.assertEqual(len(core.catalog(dbfile)['entries']),3)
            replacement=upsert(csvfile,dbfile,False,replace_existing=True)
            self.assertEqual(replacement['total_centers'],2);self.assertEqual(replacement['preserved_unmatched_centers'],0)
            self.assertEqual(len(core.catalog(dbfile)['entries']),2)
            self.assertEqual(upsert(csvfile,dbfile,False,replace_existing=True)['removed_unmatched_centers'],0)
            with core.connect(dbfile) as db:self.assertEqual(db.execute('SELECT COUNT(*) n FROM entries WHERE batch_id=?',(batch,)).fetchone()['n'],3)
    def test_duplicate_id_rejected_before_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'input.csv';self.write_csv(path,[self.raw(),self.raw()])
            with self.assertRaisesRegex(ValueError,'duplicate'):read_csv(path)

if __name__=='__main__':unittest.main()
