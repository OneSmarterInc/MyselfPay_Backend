"""Admin-only campaigns, durable SMTP queue and read-only IMAP reply import."""
from datetime import datetime, timedelta, timezone as dt_timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import make_msgid, parseaddr, parsedate_to_datetime
import hashlib
import imaplib
import json
import re
import secrets
import ssl

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, OuterRef, Subquery
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from admin_settings import secret_cipher, smtp_configuration
from api.models import (MailerCampaign, MailerContact, MailerLease, MailerMailbox,
                        MailerMessage, MailerReply, MailerTemplate)
import booking_mail
import core


def mailer_view(site, request):
    mailbox = MailerMailbox.objects.filter(pk=1).first() or MailerMailbox(pk=1)
    compose, mailbox_form = ComposeForm(), MailboxForm(instance=mailbox)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'contacts':
            count = sync_contacts()
            messages.success(request, f'{count} center email rows refreshed.')
            return redirect('admin:mailer')
        if action == 'mailbox':
            mailbox_form = MailboxForm(request.POST, instance=mailbox)
            if mailbox_form.is_valid():
                mailbox_form.save()
                messages.success(request, 'Mailbox saved. The password is encrypted and hidden.')
                return redirect('admin:mailer')
        elif action == 'sync':
            if mailbox.pk and mailbox.enabled:
                MailerMailbox.objects.filter(pk=1).update(sync_requested=True)
                messages.success(request, 'Reply sync requested. The background worker will check your inbox.')
            else:
                messages.error(request, 'Save and enable the IMAP mailbox first.')
            return redirect('admin:mailer')
        elif action == 'preview':
            compose = ComposeForm(request.POST)
            if compose.is_valid():
                try:
                    campaign = preview_campaign(compose.cleaned_data, request.user.get_username())
                except ValidationError as error:
                    compose.add_error(None, error)
                except Exception:
                    compose.add_error(None, 'Preview could not be prepared. Check SMTP sender settings and refresh the directory emails.')
                else:
                    return redirect('admin:mailer_campaign', ident=campaign.pk)
    elif request.GET.get('template'):
        template = get_object_or_404(MailerTemplate, pk=request.GET['template'])
        compose = ComposeForm(initial={'subject': template.subject, 'body': template.body, 'template_name': template.name})
    latest = MailerMessage.objects.filter(contact=OuterRef('pk')).exclude(state='draft').order_by('-campaign__created_at')
    contacts = MailerContact.objects.filter(active=True).annotate(latest_message=Subquery(latest.values('pk')[:1]))
    page = Paginator(contacts, 50).get_page(request.GET.get('page'))
    sent = {m.pk: m for m in MailerMessage.objects.filter(pk__in=[c.latest_message for c in page if c.latest_message]).annotate(reply_count=Count('replies'))}
    reply_ids = MailerReply.objects.filter(message_id=OuterRef('pk')).order_by('-received_at', '-pk')
    last_replies = MailerMessage.objects.filter(pk__in=sent).annotate(last_reply=Subquery(reply_ids.values('pk')[:1]))
    replies = {r.message_id: r for r in MailerReply.objects.filter(pk__in=[m.last_reply for m in last_replies if m.last_reply])}
    rows = [{'contact': contact, 'position': page.start_index() + n, 'last': sent.get(contact.latest_message),
             'reply_preview': reply_text(replies[contact.latest_message])[:150] if contact.latest_message in replies else ''}
            for n, contact in enumerate(page)]
    return TemplateResponse(request, 'admin/mailer.html', {**site.each_context(request), 'title': 'Mailer',
        'compose': compose, 'mailbox_form': mailbox_form, 'mailbox': mailbox, 'rows': rows, 'page': page,
        'total': contacts.count(), 'templates': MailerTemplate.objects.order_by('name'),
        'campaigns': MailerCampaign.objects.annotate(recipient_count=Count('messages')).order_by('-created_at')[:10]})


def campaign_view(site, request, ident):
    campaign = get_object_or_404(MailerCampaign, pk=ident)
    if request.method == 'POST':
        if request.POST.get('action') == 'send':
            if queue_campaign(campaign.pk):
                messages.success(request, 'Campaign queued. Delivery will run in the background.')
            else:
                messages.info(request, 'This campaign has already been queued or closed. No duplicate was sent.')
        elif request.POST.get('action') == 'cancel':
            with transaction.atomic():
                campaign = MailerCampaign.objects.select_for_update().get(pk=ident)
                if campaign.state in ('draft', 'queued'):
                    campaign.messages.filter(state__in=['draft', 'queued']).update(state='cancelled')
                    campaign.state = 'cancelled'
                    campaign.save(update_fields=['state'])
            messages.success(request, 'Unsent recipients cancelled. Messages already in progress may still be sent.')
        return redirect('admin:mailer_campaign', ident=ident)
    page = Paginator(campaign.messages.annotate(reply_count=Count('replies')).order_by('position'), 50).get_page(request.GET.get('page'))
    first = campaign.messages.order_by('position').first()
    return TemplateResponse(request, 'admin/mailer_campaign.html', {**site.each_context(request),
        'title': 'Campaign preview' if campaign.state == 'draft' else 'Campaign delivery', 'campaign': campaign,
        'page': page, 'count': campaign.messages.count(),
        'preview_subject': personalize(campaign.subject, first) if first else '',
        'preview_body': personalize(campaign.body, first) if first else '',
        'counts': campaign.messages.values('state').annotate(total=Count('pk')).order_by('state')})


def message_view(site, request, ident):
    item = get_object_or_404(MailerMessage.objects.select_related('campaign'), pk=ident)
    replies = [{'sender': reply.sender, 'subject': reply.subject, 'received_at': reply.received_at,
                'body': reply_text(reply)} for reply in item.replies.order_by('received_at')]
    return TemplateResponse(request, 'admin/mailer_message.html', {**site.each_context(request),
        'title': 'Email and replies', 'item': item, 'subject': personalize(item.campaign.subject, item),
        'body': personalize(item.campaign.body, item), 'replies': replies})


def sync_contacts():
    """One center/email per row, preserving shared addresses and import order."""
    with core.connect(settings.DOMAIN_DATABASE) as db:
        rows = db.execute('''SELECT e.entry_key, e.payload, h.first_created FROM entries e JOIN
            (SELECT entry_key,MIN(b.created) AS first_created FROM entries x
             JOIN batches b ON b.id=x.batch_id WHERE b.status='published' GROUP BY entry_key) h
            ON h.entry_key=e.entry_key WHERE e.batch_id=?''', (core.active_batch(db),)).fetchall()
    contacts = []
    for row in rows:
        entry = json.loads(row['payload'])
        address = str(entry.get('email') or '').strip().casefold()
        try:
            validate_email(address)
        except ValidationError:
            continue
        contacts.append((row['entry_key'], address, str(entry.get('name') or 'Unnamed center')[:300],
                         datetime.fromtimestamp(row['first_created'], dt_timezone.utc)))
    with transaction.atomic():
        MailerContact.objects.update(active=False)
        for key, address, name, stamp in contacts:
            MailerContact.objects.update_or_create(center_key=key, email=address, defaults={
                'centers': [name], 'imported_at': stamp, 'active': True})
    return len(contacts)



def selection_range(value, total):
    raw = str(value).strip()
    if not re.fullmatch(r'[1-9]\d{0,6}(?:\s*-\s*[1-9]\d{0,6})?', raw):
        raise ValidationError('Enter a count such as 100, or an inclusive range such as 100-200.')
    parts = re.split(r'\s*-\s*', raw)
    start, end = (1, int(parts[0])) if len(parts) == 1 else tuple(map(int, parts))
    if end < start or end > total:
        raise ValidationError(f'Choose positions between 1 and {total}; the end must follow the start.')
    return start, end


class ComposeForm(forms.Form):
    selection = forms.CharField(max_length=40, required=False, label='Recipient count or range (optional)',
                               help_text='100 selects rows 1–100. 100-200 selects rows 100–200, including both ends (101 emails).')
    selected_contacts = forms.ModelMultipleChoiceField(queryset=MailerContact.objects.filter(active=True),
        required=False, widget=forms.MultipleHiddenInput)
    subject = forms.CharField(max_length=200)
    body = forms.CharField(max_length=30000, widget=forms.Textarea(attrs={'rows': 10}), label='Message',
                           help_text='Use {{center_name}} and {{email}} for personalization. Messages are sent separately.')
    template_name = forms.CharField(max_length=100, required=False, label='Save as template (optional)')

    def clean(self):
        data = super().clean()
        if not data.get('selected_contacts') and not data.get('selection'):
            raise ValidationError('Select center checkboxes or enter a recipient count/range.')
        return data

    def clean_subject(self):
        value = self.cleaned_data['subject']
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValidationError('Use a single-line subject without control characters.')
        return value


class MailboxForm(forms.ModelForm):
    password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False),
                               help_text='Write-only. Leave blank to keep the saved password.')

    class Meta:
        model = MailerMailbox
        fields = ['enabled', 'host', 'port', 'username', 'password', 'folder', 'reply_to']
        help_texts = {'reply_to': 'Replies will be addressed to this mailbox. It must be the mailbox you monitor.',
                      'folder': 'Replies are read without deleting messages or marking them as read.'}

    def clean(self):
        data = super().clean()
        if data.get('enabled') and (not data.get('username') or not data.get('reply_to') or
                                   not (data.get('password') or self.instance.password_encrypted)):
            raise ValidationError('Enter the mailbox email, Reply-To and a password before enabling reply sync.')
        if data.get('host') and not re.fullmatch(r'[A-Za-z0-9.-]+', data['host']):
            self.add_error('host', 'Enter a hostname without a URL or port.')
        if data.get('port') and not 1 <= data['port'] <= 65535:
            self.add_error('port', 'Enter a port from 1 to 65535.')
        if data.get('folder') and (any(ord(c) < 32 for c in data['folder']) or '"' in data['folder']):
            self.add_error('folder', 'Enter a valid mailbox folder.')
        if data.get('enabled') and data.get('reply_to', '').casefold() != data.get('username', '').casefold():
            self.add_error('reply_to', 'Use the same mailbox email for Reply-To so replies reach the monitored inbox.')
        return data

    def save(self, commit=True):
        item = super().save(commit=False)
        item.pk = 1
        if self.cleaned_data.get('password'):
            item.password_encrypted = secret_cipher('mailer-imap').encrypt(self.cleaned_data['password'].encode()).decode()
        if commit:
            item.save()
        return item


def personalize(value, item):
    return value.replace('{{center_name}}', item.center_names).replace('{{email}}', item.email)


def preview_campaign(data, user):
    contacts = MailerContact.objects.filter(active=True)
    selected = data.get('selected_contacts')
    ids = [item.pk if hasattr(item, 'pk') else int(item) for item in selected] if selected is not None else []
    if ids:
        chosen = list(contacts.filter(pk__in=ids))
        if len(chosen) != len(set(ids)):
            raise ValidationError('A selected center is no longer active. Refresh and select again.')
        positions = {pk: n for n, pk in enumerate(contacts.values_list('pk', flat=True), 1)}
        selection = f'{len(chosen)} selected centers'
    else:
        start, end = selection_range(data.get('selection', ''), contacts.count())
        chosen = list(contacts[start - 1:end])
        if len(chosen) != end - start + 1:
            raise ValidationError('The directory changed. Refresh recipients and preview again.')
        positions = {item.pk: start+n for n,item in enumerate(chosen)}
        selection = data['selection']
    smtp = smtp_configuration()
    mailbox = MailerMailbox.objects.filter(pk=1).first()
    reply_to = (mailbox.reply_to if mailbox else 'patientcare@onesmarterhealthweb.com') or smtp['sender']
    validate_email(reply_to)
    with transaction.atomic():
        campaign = MailerCampaign.objects.create(subject=data['subject'], body=data['body'],
            selection=selection, reply_to=reply_to, created_by=user)
        MailerMessage.objects.bulk_create([MailerMessage(campaign=campaign, contact=item, email=item.email,
            center_names='; '.join(item.centers), position=positions[item.pk],
            message_id=make_msgid(domain=reply_to.rsplit('@', 1)[-1])) for n, item in enumerate(chosen)])
        if data.get('template_name'):
            MailerTemplate.objects.update_or_create(name=data['template_name'], defaults={
                'subject': data['subject'], 'body': data['body']})
    return campaign


def queue_campaign(ident):
    with transaction.atomic():
        campaign = MailerCampaign.objects.select_for_update().get(pk=ident)
        if campaign.state != 'draft':
            return False
        campaign.messages.filter(state='draft').update(state='queued')
        campaign.state, campaign.queued_at = 'queued', timezone.now()
        campaign.save(update_fields=['state', 'queued_at'])
    return True


def acquire_lease(name, seconds=600):
    now, token = timezone.now(), secrets.token_hex(16)
    MailerLease.objects.get_or_create(name=name, defaults={'expires': now})
    changed = MailerLease.objects.filter(name=name, expires__lte=now).update(
        token=token, expires=now + timedelta(seconds=seconds))
    return token if changed else None


def send_pending(limit=20):
    token = acquire_lease('smtp', 1200)
    if not token:
        return 0
    count = 0
    try:
        MailerMessage.objects.filter(state='sending', attempted_at__lt=timezone.now() - timedelta(minutes=10)).update(
            state='uncertain', error='Delivery was interrupted. Check the mailbox before sending a new campaign.')
        ids = list(MailerMessage.objects.filter(state='queued').order_by('campaign__queued_at', 'position').values_list('pk', flat=True)[:limit])
        for ident in ids:
            if not MailerMessage.objects.filter(pk=ident, state='queued').update(state='sending', attempted_at=timezone.now()):
                continue
            item = MailerMessage.objects.select_related('campaign').get(pk=ident)
            try:
                config = smtp_configuration()
                if not all(config[k] for k in ('host', 'username', 'password', 'sender')):
                    raise ValueError('SMTP not configured')
                allowed = {x.strip().casefold() for x in config['allowed_recipients'].split(',') if x.strip()}
                if allowed and item.email.casefold() not in allowed:
                    raise ValueError('Recipient excluded by SMTP allowlist')
                message = EmailMessage()
                message['Subject'] = personalize(item.campaign.subject, item)
                message['Message-ID'] = item.message_id
                message['Reply-To'] = item.campaign.reply_to
                message.set_content(personalize(item.campaign.body, item))
            except Exception:
                MailerMessage.objects.filter(pk=ident).update(state='failed', error='Check SMTP settings, recipient allowlist and template.')
                continue
            try:
                booking_mail.send_message(message, item.email)
            except Exception:
                # Never automatically replay a send after an ambiguous transport failure.
                MailerMessage.objects.filter(pk=ident).update(state='uncertain', error='SMTP delivery was not confirmed. Check the mailbox before resending.')
            else:
                MailerMessage.objects.filter(pk=ident).update(state='sent', sent_at=timezone.now(), error='')
                count += 1
        for campaign in MailerCampaign.objects.filter(state='queued'):
            if not campaign.messages.filter(state__in=['queued', 'sending']).exists():
                campaign.state = 'complete'
                campaign.save(update_fields=['state'])
    finally:
        MailerLease.objects.filter(name='smtp', token=token).update(expires=timezone.now())
    return count


def import_reply(raw, mailbox_identity, uid_validity, uid):
    try:
        return _import_reply(raw, mailbox_identity, uid_validity, uid)
    except (ValueError, TypeError, UnicodeError, LookupError, OverflowError):
        return False  # Malformed mail must not block later valid replies.


def _import_reply(raw, mailbox_identity, uid_validity, uid):
    mail = BytesParser(policy=policy.default).parsebytes(raw)
    references = re.findall(r'<[^<>\s]{1,250}>', str(mail.get('In-Reply-To', '')) + ' ' + str(mail.get('References', '')))[-30:]
    sender = parseaddr(str(mail.get('From', '')))[1].casefold()
    item = MailerMessage.objects.filter(message_id__in=references, email__iexact=sender,
        state__in=['sent', 'uncertain', 'sending']).order_by('-sent_at', '-pk').first()
    if not item:
        return False
    part = mail.get_body(preferencelist=('plain', 'html'))
    if part is None or part.get_content_disposition() == 'attachment':
        return False
    body = part.get_content()
    if not isinstance(body, str):
        return False
    if part.get_content_type() == 'text/html':
        body = strip_tags(body)
    try:
        stamp = parsedate_to_datetime(str(mail.get('Date', '')))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=dt_timezone.utc)
    except (ValueError, TypeError, OverflowError):
        stamp = timezone.now()
    mid = str(mail.get('Message-ID', '')).strip()
    identity = hashlib.sha256((mailbox_identity + ':' + (mid or f'{uid_validity}:{uid}')).encode()).hexdigest()
    _, created = MailerReply.objects.get_or_create(identity=identity, defaults={'message': item,
        'sender': sender, 'subject': str(mail.get('Subject', ''))[:500],
        'body_encrypted': secret_cipher('mailer-replies').encrypt(body[:50000].encode()).decode(), 'received_at': stamp})
    return created


def reply_text(reply):
    return secret_cipher('mailer-replies').decrypt(reply.body_encrypted.encode()).decode()


def sync_replies():
    token = acquire_lease('imap', 600)
    if not token:
        return 0
    mailbox = MailerMailbox.objects.filter(pk=1, enabled=True).first()
    try:
        if not mailbox:
            return 0
        oldest = MailerMessage.objects.filter(state__in=['sent', 'uncertain', 'sending']).order_by('attempted_at').first()
        if not oldest:
            MailerMailbox.objects.filter(pk=1).update(last_sync=timezone.now(), sync_status='No sent campaigns to match yet.', sync_requested=False)
            return 0
        identity = hashlib.sha256(f'{mailbox.host}:{mailbox.port}:{mailbox.username}:{mailbox.folder}'.encode()).hexdigest()
        password = secret_cipher('mailer-imap').decrypt(mailbox.password_encrypted.encode()).decode()
        imported = 0
        with imaplib.IMAP4_SSL(mailbox.host, mailbox.port, ssl_context=ssl.create_default_context(), timeout=20) as client:
            client.login(mailbox.username, password)
            result, _ = client.select('"' + mailbox.folder + '"', readonly=True)
            if result != 'OK':
                raise ValueError('Mailbox folder unavailable')
            validity = (client.response('UIDVALIDITY')[1] or [b''])[0].decode()
            if not validity.isdigit():
                raise ValueError('Missing UIDVALIDITY')
            cursor = mailbox.last_uid if mailbox.cursor_identity == identity and mailbox.uid_validity == validity else 0
            since = ((oldest.attempted_at or timezone.now()) - timedelta(days=1)).strftime('%d-%b-%Y')
            result, data = client.uid('search', None, 'UID', f'{cursor + 1}:*', 'SINCE', since)
            if result != 'OK':
                raise ValueError('Search failed')
            ids = sorted(int(uid) for uid in (data[0] or b'').split() if uid.isdigit() and int(uid) > cursor)[:200]
            for uid in ids:
                result, chunks = client.uid('fetch', str(uid), '(BODY.PEEK[HEADER.FIELDS (IN-REPLY-TO REFERENCES FROM)]<0.16384> RFC822.SIZE)')
                if result != 'OK':
                    raise ValueError('Header fetch failed')
                parts = [p for p in chunks if isinstance(p, tuple)]
                if parts:
                    header = BytesParser(policy=policy.default).parsebytes(parts[0][1])
                    refs = re.findall(r'<[^<>\s]{1,250}>', str(header.get('In-Reply-To', '')) + ' ' + str(header.get('References', '')))[-30:]
                    sender = parseaddr(str(header.get('From', '')))[1].casefold()
                    sizes = re.findall(rb'RFC822.SIZE\s+(\d+)', b' '.join(p[0] for p in parts))
                    matched = MailerMessage.objects.filter(message_id__in=refs, email__iexact=sender).exists()
                    if matched and sizes and int(sizes[0]) <= 1024 * 1024:
                        status, messages = client.uid('fetch', str(uid), '(BODY.PEEK[])')
                        if status != 'OK':
                            raise ValueError('Message fetch failed')
                        for chunk in messages:
                            if isinstance(chunk, tuple):
                                imported += int(import_reply(chunk[1], identity, validity, uid))
                MailerMailbox.objects.filter(pk=1, host=mailbox.host, username=mailbox.username, folder=mailbox.folder).update(
                    cursor_identity=identity, uid_validity=validity, last_uid=uid)
        MailerMailbox.objects.filter(pk=1).update(last_sync=timezone.now(), sync_status=f'Reply sync complete: {imported} new replies.', sync_requested=False)
        return imported
    except Exception:
        if mailbox:
            MailerMailbox.objects.filter(pk=1).update(last_sync=timezone.now(), sync_status='Reply sync failed. Check the IMAP mailbox, password and SSL connection.', sync_requested=False)
        return 0
    finally:
        MailerLease.objects.filter(name='imap', token=token).update(expires=timezone.now())
