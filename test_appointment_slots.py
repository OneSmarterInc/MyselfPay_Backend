"""Preferred/custom appointment approval persists across verified center flows."""
from datetime import datetime, timedelta
import unittest
import booking
import center_portal
import appointment_slots
import test_booking
from test_center_portal import CenterPortalTests


class AppointmentTests(unittest.TestCase):
    setUp = test_booking.BookingTests.setUp
    tearDown = test_booking.BookingTests.tearDown
    verify_email = test_booking.BookingTests.verify_email
    verify_phone = test_booking.BookingTests.verify_phone
    create = test_booking.BookingTests.create
    tokens = test_booking.BookingTests.tokens
    authorize_clinic = test_booking.BookingTests.authorize_clinic
    login = CenterPortalTests.login

    def test_approval_requires_choice_and_persists_one_preferred_slot(self):
        item = self.create()
        session = self.authorize_clinic(item)
        with self.assertRaises(booking.BookingError):
            booking.clinic_details(self.db, item['id'], session, 'scheduled')
        with booking.connection(self.db) as db:
            self.assertEqual(booking.get_row(db,item['id'])['status'],'requested')
        approved = booking.clinic_details(self.db,item['id'],session,'scheduled',{'preferred_index':1})
        self.assertEqual(approved['approved_slot']['date'], self.data['slots'][1]['date'])
        self.assertEqual(approved['approved_slot']['source'], 'preferred')
        self.assertEqual(approved['approved_slot']['period'], self.data['slots'][1]['period'])
        self.assertNotIn('phone', approved)
        receipt = booking.patient(self.db,item['id'],item['tracking_token'])
        self.assertEqual(receipt['approved_slot'],approved['approved_slot'])
        self.assertEqual(booking.clinic_details(self.db,item['id'],session,'scheduled',{'preferred_index':1})['approved_slot'],receipt['approved_slot'])
        with self.assertRaises(booking.BookingError):
            booking.clinic_details(self.db,item['id'],session,'scheduled',{'preferred_index':0})

    def test_center_portal_can_confirm_an_alternative_and_dashboard_reads_it(self):
        item = self.create()
        token, csrf = self.login()
        choice = {'date':(datetime.now()+timedelta(days=6)).date().isoformat(),'time':'14:30'}
        approved = center_portal.open_request(self.db,token,csrf,item['id'],'scheduled',choice)
        slot = approved['approved_slot']
        self.assertEqual(slot, {**choice,'timezone':self.data['timezone'],'source':'custom'})
        self.assertEqual(center_portal.dashboard(self.db,token)['requests'][0]['approved_slot'],slot)
        self.assertIn('2:30 PM',appointment_slots.label(slot))
        self.assertEqual(booking.patient(self.db,item['id'],item['tracking_token'])['approved_slot'],slot)

    def test_invalid_past_out_of_range_and_ambiguous_times_are_rejected(self):
        item = self.create()
        session = self.authorize_clinic(item)
        for choice in (None, {}, {'preferred_index':True}, {'preferred_index':3},
                       {'date':'bad','time':'14:00'}, {'date':'2001-01-01','time':'14:00'},
                       {'date':(datetime.now()+timedelta(days=100)).date().isoformat(),'time':'14:00'},
                       {'date':self.data['slots'][0]['date'],'time':'25:10'}):
            with self.subTest(choice=choice),self.assertRaises(booking.BookingError):
                booking.clinic_details(self.db,item['id'],session,'scheduled',choice)
        for date in ('2027-03-14','2026-11-01'):
            with self.assertRaises(booking.BookingError):
                appointment_slots.selected({'timezone':'America/New_York','slots':[]},{'date':date,'time':'02:30' if '03-14' in date else '01:30'},False)

    def test_nonapproval_outcomes_need_no_slot_and_legacy_approvals_are_honest(self):
        item = self.create()
        session = self.authorize_clinic(item)
        followed = booking.clinic_details(self.db,item['id'],session,'voicemail')
        self.assertIsNone(followed['approved_slot'])
        closed = booking.clinic_details(self.db,item['id'],session,'unable')
        self.assertEqual(closed['status'],'unable')
        self.assertIsNone(closed['approved_slot'])
        self.assertIn('not recorded',appointment_slots.label(None))
