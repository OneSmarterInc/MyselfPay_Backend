from django.core.management.base import BaseCommand, CommandError
import booking_sms
import booking


class Command(BaseCommand):
    help = 'Check Twilio Verify credentials without displaying secrets. Sends no SMS unless --send is supplied.'

    def add_arguments(self, parser):
        parser.add_argument('--send', action='store_true', help='Send one diagnostic SMS OTP.')
        parser.add_argument('--phone', help='Destination mobile with country code; required with --send.')

    def handle(self, **options):
        if options['send'] and not options['phone']:
            raise CommandError('Use --send --phone followed by your international mobile number.')
        try:
            result=booking_sms.check_service()
            self.stdout.write('Twilio Verify authentication and service lookup: OK. No credentials displayed.')
            length=result.get('code_length')
            if length is not None and length != 6:
                self.stdout.write(self.style.WARNING('Set this Verify service code length to 6 to match the booking form.'))
            if options['send']:
                from booking_phone import phone_number
                phone=phone_number(options['phone'])
                booking_sms.send_code(phone)
                self.stdout.write(self.style.SUCCESS('Twilio accepted the diagnostic SMS verification request. Check your phone; acceptance does not confirm delivery.'))
            else:
                self.stdout.write('No SMS sent. To diagnose delivery, use --send --phone with your own mobile number.')
        except booking.BookingError as exc:
            raise CommandError(str(exc)) from None
        except booking_sms.SMSUnavailable as exc:
            if exc.http_status:
                code=exc.provider_code or 'unknown'
                hint={20003:'Check API key/secret, key permissions, account ownership and region.',
                      20404:'Check the Verify Service SID and whether it belongs to this account.',
                      21608:'Check Twilio trial recipient verification and account restrictions.'}.get(exc.provider_code,'Check the corresponding error in Twilio Verify logs.')
                raise CommandError(f'Twilio rejected the request: HTTP {exc.http_status}; Twilio error {code}. {hint}') from None
            raise CommandError(str(exc)) from None
