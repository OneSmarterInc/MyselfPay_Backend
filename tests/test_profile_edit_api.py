"""Edit name and Home ZIP over HTTP: synthetic US phone and mocked Twilio only."""
import unittest
import core
from tests import test_api as fixtures
from tests import test_us_request_auth as us_auth


class ProfileEditAPITests(unittest.TestCase):
    setUp = fixtures.APITests.setUp
    tearDown = fixtures.APITests.tearDown
    post = fixtures.APITests.post
    issue = us_auth.USRequestTests.issue
    finish = us_auth.USRequestTests.finish

    def test_name_and_zip_change_and_zip_can_be_cleared(self):
        ident = self.finish(self.issue()).json()['member']['id']
        done = self.post('account/profile', {'name': '  Vikram  ', 'zip_code': '45402'})
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(done.json(), {'name': 'Vikram', 'zip_code': '45402'})
        again = self.post('account/profile', {'zip_code': '45459'})
        self.assertEqual(again.json(), {'name': 'Vikram', 'zip_code': '45459'})
        cleared = self.post('account/profile', {'zip_code': ''})
        self.assertEqual(cleared.json(), {'name': 'Vikram', 'zip_code': ''})
        with core.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT name FROM members WHERE id=?', (ident,)).fetchone()['name'], 'Vikram')
            self.assertIsNone(db.execute('SELECT 1 FROM account_zip_codes WHERE member_id=?', (ident,)).fetchone())

    def test_bad_values_are_refused_and_nothing_changes(self):
        self.finish(self.issue())
        self.post('account/profile', {'name': 'Vikram'})
        for body in ({'name': ''}, {'name': 'x' * 101}, {'zip_code': '4540'}, {'zip_code': 'abcde'}, {}):
            response = self.post('account/profile', body)
            self.assertEqual(response.status_code, 400, (body, response.content))
        self.assertEqual(self.post('account/profile', {'zip_code': ''}).json()['name'], 'Vikram')

    def test_a_signed_in_member_is_required(self):
        self.assertIn(self.post('account/profile', {'name': 'X'}).status_code, (401, 403))


if __name__ == '__main__':
    unittest.main()
