"""TLS SMTP transport for patient verification, center requests and clinic OTPs."""
from email.message import EmailMessage
from email.headerregistry import Address
from email.errors import MessageError
import os
import smtplib
import ssl


class MailUnavailable(RuntimeError):
    pass


class MailDeliveryUncertain(MailUnavailable):
    pass


def send_message(message, recipient):
    host = os.environ.get('SMTP_HOST', '')
    username = os.environ.get('SMTP_USERNAME', '')
    password = os.environ.get('SMTP_PASSWORD', '')
    sender = os.environ.get('SMTP_FROM', username)
    use_ssl = os.getenv('SMTP_USE_SSL', 'false').lower() == 'true'
    use_tls = os.getenv('SMTP_USE_TLS', 'true').lower() == 'true'
    if not all((host, username, password, sender)):
        raise MailUnavailable('Email delivery is not configured. Please contact support.')
    if use_ssl == use_tls:
        raise MailUnavailable('Configure exactly one of SMTP_USE_TLS or SMTP_USE_SSL.')
    if not recipient or '\r' in recipient or '\n' in recipient:
        raise MailUnavailable('A valid email recipient is required.')
    try:
        address = Address(os.getenv('SMTP_FROM_NAME', 'MySelfPay'), addr_spec=sender)
        if not address.domain or not address.username:
            raise ValueError('Sender address requires a domain.')
        if 'From' in message:
            message.replace_header('From', str(address))
        else:
            message['From'] = str(address)
    except (ValueError, TypeError, MessageError):
        raise MailUnavailable('Configure SMTP_FROM as a valid sender email address.') from None
    if 'To' in message:
        message.replace_header('To', recipient)
    else:
        message['To'] = recipient
    for header in ('Bcc', 'Cc'):
        if header in message:
            del message[header]
    if 'Message-ID' not in message:
        from email.utils import make_msgid
        message['Message-ID'] = make_msgid(domain=sender.rsplit('@', 1)[-1])
    if 'Date' not in message:
        from email.utils import formatdate
        message['Date'] = formatdate(localtime=False)
    accepted = False
    try:
        port = int(os.getenv('SMTP_PORT', '465' if use_ssl else '587'))
        context = ssl.create_default_context()
        transport = smtplib.SMTP_SSL(host, port, timeout=15, context=context) if use_ssl else smtplib.SMTP(host, port, timeout=15)
        with transport as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(username, password)
            try:
                refused = smtp.send_message(message, from_addr=sender, to_addrs=[recipient])
            except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
                raise MailUnavailable('The email was rejected by the mail server.') from None
            except (OSError, smtplib.SMTPException):
                raise MailDeliveryUncertain('The mail server did not confirm acceptance.') from None
            if refused:
                raise MailUnavailable('The email recipient was rejected.')
            accepted = True
    except MailUnavailable:
        raise
    except (OSError, ValueError, smtplib.SMTPException):
        if not accepted:
            raise MailUnavailable('Email could not be sent. Please retry shortly.') from None


def send_clinic_code(recipient, ident, code):
    message = EmailMessage()
    message['From'] = 'MySelfPay'
    message['Subject'] = f'MySelfPay access code for request {ident}'
    message.set_content(f'Your MySelfPay clinic access code is: {code}\n\nRequest: {ident}\nThis code expires in 10 minutes and can be used once.\nEnter it on the secure request page. Do not forward this code.\n\nMySelfPay\nPlease do not reply to this automated email.')
    send_message(message, recipient)


def send_patient_code(recipient: str, code: str) -> None:
    host = os.environ.get('SMTP_HOST', '')
    username = os.environ.get('SMTP_USERNAME', '')
    password = os.environ.get('SMTP_PASSWORD', '')
    sender = os.environ.get('SMTP_FROM', username)
    if not all((host, username, password, sender)):
        raise MailUnavailable('Email verification is not configured. Please contact support.')
    message = EmailMessage()
    message['From'] = Address('MySelfPay', addr_spec=sender)
    message['To'] = recipient
    message['Subject'] = 'Your MySelfPay email verification code'
    message.set_content(
        f'Your MySelfPay verification code is: {code}\n\n'
        'This code expires in 10 minutes and can be used once.\n'
        'Enter it on MySelfPay to verify your email before requesting an appointment.\n'
        'This does not confirm an appointment.\n\n'
        'If you did not request this code, ignore this email.\n\n'
        'MySelfPay by One Smarter, Inc.\n'
        'Please do not reply to this automated email.'
    )
    message.add_alternative(f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#f4f5f6;font-family:Arial,Helvetica,sans-serif;color:#22272b">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border:1px solid #e2e5e8;border-radius:14px">
<tr><td style="padding:28px 32px;border-bottom:1px solid #eceef0"><span style="font-size:26px;font-weight:700">MySelf<span style="color:#b51d2b">Pay</span></span><br><span style="font-size:12px;color:#68727a">by One Smarter, Inc.</span></td></tr>
<tr><td style="padding:32px"><p style="font-size:12px;font-weight:700;letter-spacing:1px;color:#b51d2b;margin:0 0 12px">VERIFY YOUR EMAIL</p>
<h1 style="font-size:24px;line-height:1.3;margin:0 0 16px">One step closer to your request.</h1>
<p style="font-size:16px;line-height:1.6">Enter this code on MySelfPay to verify your email before submitting your appointment request.</p>
<div style="background:#fff2f3;border:1px solid #f1d5d8;border-radius:10px;text-align:center;padding:22px 10px;margin:24px 0;font-size:36px;letter-spacing:8px;font-weight:700;color:#a81725">{code}</div>
<p style="font-size:14px;line-height:1.6">Your code expires in <strong>10 minutes</strong> and can be used once. Keep it private.</p>
<p style="font-size:14px;line-height:1.6;color:#68727a">This verifies your email only; it does not confirm an appointment. If you did not request this code, you can ignore this email.</p>
</td></tr><tr><td style="padding:20px 32px;background:#fafafa;border-top:1px solid #eceef0;font-size:12px;line-height:1.6;color:#68727a">MySelfPay by One Smarter, Inc.<br><strong>Please do not reply to this automated email.</strong></td></tr>
</table></td></tr></table></body></html>''', subtype='html')
    send_message(message, recipient)

