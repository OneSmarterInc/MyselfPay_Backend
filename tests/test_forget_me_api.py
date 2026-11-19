"""Forget me and cancel over HTTP: synthetic US phone and mocked Twilio only."""
import unittest
from unittest.mock import patch
import core
from tests import test_api as fixtures
from tests import test_us_request_auth as us_auth


class ForgetMeAPITests(unittest.TestCase):
    setUp = fixtures.APITests.setUp
    tearDown = fixtures.APITests.tearDown
    post = fixtures.APITests.post
    issue = us_auth.USRequestTests.issue
    finish = us_auth.USRequestTests.finish

    def test_forget_me_needs_a_texted_code_then_removes_the_account(self):
        ident = self.finish(self.issue()).json()['member']['id']
        with patch('booking_sms.configuration', return_value={}), patch('booking_sms.send_code', return_value='VE' + '1' * 32) as send:
            sent = self.post('account/forget', {})
        self.assertEqual(sent.status_code, 200, sent.content)
        self.assertEqual(send.call_args.args[0], '+16145550142')
        with patch('booking_sms.check_code', return_value=False):
            wrong = self.post('account/forget', {'verification_id': sent.json()['verification_id'], 'code': '000000'})
        self.assertEqual(wrong.status_code, 400, wrong.content)
        with core.connect(self.db) as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM members WHERE id=?', (ident,)).fetchone())
        with patch('booking_sms.check_code', return_value=True):
            done = self.post('account/forget', {'verification_id': sent.json()['verification_id'], 'code': '123456'})
        self.assertEqual(done.status_code, 200, done.content)
        self.assertTrue(done.json()['deleted'])
        with core.connect(self.db) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM members WHERE id=?', (ident,)).fetchone())

    def test_forget_me_and_cancel_need_a_signed_in_member(self):
        self.assertIn(self.post('account/forget', {}).status_code, (401, 403))
        self.assertIn(self.post('booking/cancel', {'id': 'MSP-X'}).status_code, (401, 403))

    def test_cancel_of_an_unknown_request_is_not_found(self):
        self.finish(self.issue())
        response = self.post('booking/cancel', {'id': 'MSP-DOESNOTEXIST'})
        self.assertEqual(response.status_code, 404, response.content)


if __name__ == '__main__':
    unittest.main()
