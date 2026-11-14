"""Synthetic Twilio responses for account tests; never sends an SMS."""
from unittest.mock import patch
import mobile_accounts


def proof(path, phone):
    with patch('booking_sms.configuration', return_value={}), patch('booking_sms.send_code', return_value='VE'+'1'*32):
        issued = mobile_accounts.request_code(path, {'phone': phone}, 'synthetic-test')
    with patch('booking_sms.check_code', return_value=True):
        checked = mobile_accounts.verify_code(path, {**issued, 'code':'123456'})
    return {'phone':phone, 'challenge':issued['challenge'], **checked}
