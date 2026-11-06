"""US numbering-plan validation; successful OTP proves control of the number."""
import re
import phonenumbers
import booking


def normalize(value):
    if not isinstance(value, str) or not re.fullmatch(r'[+0-9 ().-]{7,30}', value.strip()):
        raise booking.BookingError('Enter a valid US phone number that can receive an SMS code.')
    try:
        number = phonenumbers.parse(value, 'US')
    except phonenumbers.NumberParseException:
        raise booking.BookingError('Enter a valid US phone number that can receive an SMS code.') from None
    if not phonenumbers.is_valid_number_for_region(number, 'US'):
        raise booking.BookingError('A valid US phone number is required to request a time.')
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def eligible(value):
    try:
        normalize(value)
        return True
    except booking.BookingError:
        return False
