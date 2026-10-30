"""Patient verification mail only. Clinic delivery has no enabled transport."""
from email.message import EmailMessage
from email.headerregistry import Address
import os
import smtplib
import ssl


class MailUnavailable(RuntimeError):
    pass


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
    try:
        port = int(os.environ.get('SMTP_PORT', '587'))
        if port != 587:
            raise ValueError('Only STARTTLS submission on port 587 is configured.')
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(username, password)
            refused = smtp.send_message(message, from_addr=sender, to_addrs=[recipient])
            if refused:
                raise MailUnavailable('The verification email could not be sent. Please retry.')
    except (OSError, ValueError, smtplib.SMTPException):
        raise MailUnavailable('The verification email could not be sent. Please retry shortly.') from None

