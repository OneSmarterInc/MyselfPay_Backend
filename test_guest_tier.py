import concurrent.futures
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import core

LIMITS=(3,5,500,30,2000)

class AgentChecks:
    def test_reservation_precedes_generation_and_rejection_skips_it(self):
        self.reset();g=core.guest_login(self.db)
        def generate(*args):
            self.assertEqual(sum(core.guest_usage(g['token'],self.db).values()),1)
            return {}
        with patch('core.agent',side_effect=generate) as model:
            r=self.send(g)
            self.assertEqual(model.call_count,1)
        with patch('core.agent') as model:
            with self.assertRaises(PermissionError):self.send({'token':'invalid'})
            model.assert_not_called()
        for _ in range(4):self.send(g,r['conversation_id'])
        with patch('core.agent') as model:
            with self.assertRaises(core.GuestAgentLimited):self.send(g,r['conversation_id'])
            model.assert_not_called()
    def test_failed_generation_keeps_reservation(self):
        self.reset();g=core.guest_login(self.db)
        with patch('core.agent',side_effect=RuntimeError('synthetic generation failure')):
            with self.assertRaises(RuntimeError):self.send(g)
        self.assertEqual(sum(core.guest_usage(g['token'],self.db).values()),1)
    def reset(self):
        with core.connect(self.db) as db:db.execute('DELETE FROM login_limits')
    def send(self,guest,cid=None,text='MRI',ip='synthetic-ip'):
        return core.guest_agent(guest['token'],ip,text,{},cid,LIMITS,self.db)
    def test_conversation_and_message_boundaries(self):
        self.reset();g=core.guest_login(self.db)
        for _ in range(3):
            r=self.send(g);cid=r['conversation_id']
            for _ in range(4):r=self.send(g,cid)
            self.assertEqual(r['usage'][cid],5)
            with self.assertRaises(core.GuestAgentLimited):self.send(g,cid)
        with self.assertRaises(core.GuestAgentLimited):self.send(g)
        self.assertEqual(len(core.guest_usage(g['token'],self.db)),3)
    def test_message_size_and_no_content_storage(self):
        self.reset();g=core.guest_login(self.db)
        with self.assertRaises(core.GuestAgentLimited):self.send(g,text='x'*501)
        self.assertEqual(core.guest_usage(g['token'],self.db),{})
        self.send(g,text='x'*500)
        with core.connect(self.db) as db:
            row=db.execute('SELECT payload FROM guest_usage WHERE member_id=?',(g['member']['id'],)).fetchone()
            self.assertNotIn('xxx',row['payload'])
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM conversations WHERE member_id=?',(g['member']['id'],)).fetchone()['n'],0)
    def test_ip_and_global_limits_and_rollover(self):
        self.reset()
        with patch('core.time.time',return_value=172800):
            guests=[core.guest_login(self.db) for _ in range(3)]
            for g in guests[:2]:
                for _ in range(3):
                    r=self.send(g)
                    for _ in range(4):self.send(g,r['conversation_id'])
            with self.assertRaises(core.GuestAgentLimited) as caught:self.send(guests[2])
            self.assertEqual(caught.exception.reason,"hourly")
            self.send(guests[2],ip='other-ip')
            with core.connect(self.db) as db:
                db.execute('UPDATE login_limits SET attempts=1999 WHERE key=?',(hashlib.sha256(b'guest-agent-global').hexdigest(),))
            self.send(guests[2],ip='third-ip')
            with self.assertRaises(core.GuestAgentLimited) as caught:self.send(guests[2],ip='fourth-ip')
            self.assertEqual(caught.exception.reason,"daily")
        with patch('core.time.time',return_value=259200):
            g=core.guest_login(self.db);self.send(g)
    def test_hour_rollover_releases_ip_limit(self):
        self.reset()
        with patch('core.time.time',return_value=172800):
            g=core.guest_login(self.db)
            with core.connect(self.db) as db:
                db.execute('INSERT INTO login_limits VALUES(?,?,?)',(hashlib.sha256(b'guest-agent-ip:synthetic-ip').hexdigest(),2880,30))
            with self.assertRaises(core.GuestAgentLimited):self.send(g)
        with patch('core.time.time',return_value=176400):self.send(g)

    def test_usage_survives_refresh_and_isolation(self):
        self.reset();a=core.guest_login(self.db);b=core.guest_login(self.db)
        r=self.send(a)
        with self.assertRaises(ValueError):self.send(b,r['conversation_id'])
        self.assertEqual(core.guest_usage(a['token'],self.db)[r['conversation_id']],1)
        self.assertEqual(core.guest_usage(b['token'],self.db),{})
        with core.connect(self.db) as db:db.execute('UPDATE sessions SET expires=0 WHERE member_id=?',(a['member']['id'],))
        with self.assertRaises(PermissionError):self.send(a,r['conversation_id'])
    def test_simultaneous_messages_cannot_exceed_five(self):
        self.reset();g=core.guest_login(self.db);r=self.send(g)
        def attempt(_):
            try:self.send(g,r['conversation_id']);return True
            except core.GuestAgentLimited:return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt,range(10))),4)

class SQLiteAgentChecks(AgentChecks,unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Path(self.temp.name)/'test.db';core.initialize(self.db)
    def tearDown(self):self.temp.cleanup()

@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'Disposable PostgreSQL database required')
class PostgresAgentChecks(AgentChecks,unittest.TestCase):
    def setUp(self):self.db=os.environ['TEST_DATABASE_URL'];core.initialize(self.db)



