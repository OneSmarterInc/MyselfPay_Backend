import concurrent.futures
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import core

class GuestLimitChecks:
    def clear_quota(self):
        with core.connect(self.db) as db:
            db.execute("DELETE FROM settings WHERE key='guest_usage_lock'")
            db.execute("DELETE FROM login_limits")
    def count_guests(self):
        with core.connect(self.db) as db:
            return db.execute("SELECT COUNT(*) AS n FROM members WHERE tenant='guest'").fetchone()['n']
    def test_minute_and_day_limits(self):
        self.clear_quota()
        with patch.dict(os.environ,{'GUEST_GLOBAL_PER_HOUR':'2','GUEST_GLOBAL_PER_DAY':'3'}):
            with patch('core.time.time',return_value=172800):
                guest=core.guest_login(self.db)
                core.guest_login(self.db)
                before=self.count_guests()
                with self.assertRaises(core.GuestCreationLimited):core.guest_login(self.db)
                self.assertEqual(self.count_guests(),before)
                self.assertIsNotNone(core.session(guest['token'],self.db))
            with patch('core.time.time',return_value=176401):
                core.guest_login(self.db)
                with self.assertRaises(core.GuestCreationLimited) as error:core.guest_login(self.db)
                self.assertGreater(error.exception.retry_after,60)
            with patch('core.time.time',return_value=259200):core.guest_login(self.db)
    def test_concurrent_admission(self):
        self.clear_quota()
        before=self.count_guests()
        with patch.dict(os.environ,{'GUEST_GLOBAL_PER_HOUR':'3','GUEST_GLOBAL_PER_DAY':'3'}),patch('core.time.time',return_value=345600):
            def attempt(_):
                try:core.guest_login(self.db);return True
                except core.GuestCreationLimited:return False
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(sum(pool.map(attempt,range(12))),3)
        self.assertEqual(self.count_guests()-before,3)
    def test_failed_creation_rolls_back_counter(self):
        self.clear_quota()
        with core.connect(self.db) as db:
            db.execute('INSERT INTO members VALUES(?,?,?,?,?,?)',('collision','guest','collision@guest.invalid','Guest','x','x'))
        with patch('core.uuid.uuid4',return_value='collision'):
            with self.assertRaises(Exception):core.guest_login(self.db)
        with core.connect(self.db) as db:
            row=db.execute("SELECT value FROM settings WHERE key='guest_usage_lock'").fetchone()
            self.assertIsNone(row)
            db.execute("DELETE FROM members WHERE id='collision'")

class SQLiteGuestLimits(GuestLimitChecks, unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Path(self.temp.name)/'test.sqlite'
        core.initialize(self.db)
    def tearDown(self):self.temp.cleanup()

@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'Disposable PostgreSQL database required')
class PostgresGuestLimits(GuestLimitChecks,unittest.TestCase):
    def setUp(self):
        self.db=os.environ['TEST_DATABASE_URL']
        core.initialize(self.db)


