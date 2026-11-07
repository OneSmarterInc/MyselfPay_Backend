"""All mail and IMAP calls are mocked; never contact a real mailbox."""
from datetime import timedelta
from email.message import EmailMessage
from unittest.mock import patch, MagicMock
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from admin_settings import secret_cipher
from api.models import MailerContact, MailerMailbox, MailerMessage, MailerReply, MailerCampaign
import admin_mailer


@override_settings(ALLOWED_HOSTS=['testserver'], FRONTEND_ORIGIN='http://testserver',
                   CSRF_TRUSTED_ORIGINS=['http://testserver'], SECURE_SSL_REDIRECT=False,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class MailerTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('mailer-admin', password='Synthetic-password!')
        self.client.force_login(self.admin)
        self.client.defaults['HTTP_ORIGIN'] = 'http://testserver'
        now = timezone.now()
        MailerContact.objects.bulk_create([MailerContact(email=f'center{n:03d}@example.test',
            centers=[f'Synthetic center {n}'], imported_at=now - timedelta(days=n)) for n in range(1, 206)])
        self.config = {'host': 'smtp.example.test', 'username': 'sender@example.test', 'password': 'synthetic',
                       'sender': 'sender@example.test', 'allowed_recipients': ''}
        self.patch = patch('admin_mailer.smtp_configuration', return_value=self.config)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.data = {'selection': '2', 'subject': 'Hello {{center_name}}', 'body': 'Contact {{email}}', 'template_name': ''}

    def preview(self, selection='2'):
        return admin_mailer.preview_campaign({**self.data, 'selection': selection}, 'mailer-admin')

    def test_selection_is_one_based_inclusive_and_frozen(self):
        self.assertEqual(admin_mailer.selection_range('100', 205), (1, 100))
        self.assertEqual(admin_mailer.selection_range('100-200', 205), (100, 200))
        for value in ('0', '-1', '10-2', '206', 'x', '1-206'):
            with self.assertRaises(ValidationError):
                admin_mailer.selection_range(value, 205)
        campaign = self.preview('100-200')
        self.assertEqual(campaign.messages.count(), 101)
        self.assertEqual(campaign.messages.order_by('position').first().email, 'center100@example.test')
        MailerContact.objects.create(email='new@example.test', centers=['New'], imported_at=timezone.now())
        self.assertEqual(campaign.messages.order_by('position').last().email, 'center200@example.test')

    def test_preview_does_not_send_and_confirm_is_idempotent(self):
        with patch('booking_mail.send_message') as send:
            campaign = self.preview()
            self.assertEqual(campaign.state, 'draft')
            self.assertTrue(admin_mailer.queue_campaign(campaign.pk))
            self.assertFalse(admin_mailer.queue_campaign(campaign.pk))
            send.assert_not_called()
            self.assertEqual(admin_mailer.send_pending(), 2)
            self.assertEqual(admin_mailer.send_pending(), 0)
            self.assertEqual(send.call_count, 2)
            message, recipient = send.call_args.args
            self.assertEqual(message['Reply-To'], 'patientcare@onesmarterhealthweb.com')
            self.assertIn('Synthetic center', message['Subject'])
            self.assertEqual(len(message.get_all('Reply-To')), 1)
            self.assertTrue(recipient.endswith('@example.test'))
        self.assertEqual(campaign.messages.filter(state='sent').count(), 2)

    def test_delivery_failure_is_not_automatically_replayed(self):
        campaign = self.preview('1')
        admin_mailer.queue_campaign(campaign.pk)
        with patch('booking_mail.send_message', side_effect=TimeoutError()) as send:
            self.assertEqual(admin_mailer.send_pending(), 0)
            admin_mailer.send_pending()
            self.assertEqual(send.call_count, 1)
        self.assertEqual(campaign.messages.get().state, 'uncertain')

    def test_allowlist_and_cancellation_prevent_sends(self):
        campaign = self.preview('1')
        admin_mailer.queue_campaign(campaign.pk)
        self.config['allowed_recipients'] = 'different@example.test'
        with patch('booking_mail.send_message') as send:
            admin_mailer.send_pending()
            send.assert_not_called()
        self.assertEqual(campaign.messages.get().state, 'failed')
        draft = self.preview('1')
        self.client.post(f'/admin/mailer/campaign/{draft.pk}/', {'action': 'cancel'})
        self.assertFalse(admin_mailer.queue_campaign(draft.pk))

    def reply(self, item, sender=None, mid='<reply@example.test>'):
        mail = EmailMessage()
        mail['From'] = sender or item.email
        mail['Message-ID'] = mid
        mail['In-Reply-To'] = item.message_id
        mail['Subject'] = 'Re: Your request'
        mail.set_content('Thanks. <script>unsafe()</script>')
        return mail.as_bytes()

    def test_reply_matches_reference_and_sender_and_is_deduplicated_encrypted(self):
        item = self.preview('1').messages.get()
        item.state = 'sent'
        item.save()
        raw = self.reply(item)
        self.assertTrue(admin_mailer.import_reply(raw, 'mailbox', '1', 10))
        self.assertFalse(admin_mailer.import_reply(raw, 'mailbox', '2', 20))
        self.assertFalse(admin_mailer.import_reply(self.reply(item, 'wrong@example.test', '<wrong@example.test>'), 'mailbox', '1', 11))
        reply = MailerReply.objects.get()
        self.assertNotIn('Thanks', reply.body_encrypted)
        response = self.client.get(f'/admin/mailer/message/{item.pk}/')
        self.assertContains(response, '&lt;script&gt;unsafe()&lt;/script&gt;')
        self.assertNotContains(response, '<script>unsafe()</script>')

    def test_mailbox_secret_is_write_only_and_admin_access_is_enforced(self):
        response = self.client.post('/admin/mailer/', {'action': 'mailbox', 'enabled': 'on',
            'host': 'imap.example.test', 'port': 993, 'username': 'reply@example.test',
            'reply_to': 'reply@example.test', 'folder': 'INBOX', 'password': 'synthetic-imap-secret'})
        self.assertEqual(response.status_code, 302)
        saved = MailerMailbox.objects.get()
        self.assertNotIn('synthetic-imap-secret', saved.password_encrypted)
        self.assertNotContains(self.client.get('/admin/mailer/'), 'synthetic-imap-secret')
        self.assertContains(self.client.get('/admin/mailer/'), 'Reply mailbox')
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post('/admin/mailer/', {'action': 'contacts'}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get('/admin/mailer/').status_code, 302)
        staff = get_user_model().objects.create_user('staff', password='Synthetic-password!', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get('/admin/mailer/').status_code, 302)

    def test_imap_uses_read_only_and_peek_and_only_imports_matched_message(self):
        item = self.preview('1').messages.get()
        item.state, item.attempted_at = 'sent', timezone.now()
        item.save()
        MailerMailbox.objects.create(enabled=True, host='imap.example.test', username='reply@example.test',
            reply_to='reply@example.test', password_encrypted=secret_cipher('mailer-imap').encrypt(b'synthetic').decode())
        mail = self.reply(item)
        header = f'From: {item.email}\r\nIn-Reply-To: {item.message_id}\r\n\r\n'.encode()
        client = MagicMock()
        client.select.return_value = ('OK', [b'1'])
        client.response.return_value = ('UIDVALIDITY', [b'123'])
        client.uid.side_effect = [('OK', [b'10']), ('OK', [(b'1 (RFC822.SIZE 1000)', header), b')']),
                                  ('OK', [(b'1 BODY', mail), b')'])]
        with patch('admin_mailer.imaplib.IMAP4_SSL') as transport:
            transport.return_value.__enter__.return_value = client
            self.assertEqual(admin_mailer.sync_replies(), 1)
        client.select.assert_called_once_with('"INBOX"', readonly=True)
        self.assertIn('BODY.PEEK', client.uid.call_args_list[-1].args[-1])
        self.assertEqual(MailerMailbox.objects.get().last_uid, 10)

    def test_preview_pages_and_template_library(self):
        response = self.client.post('/admin/mailer/', {'action': 'preview', **self.data, 'template_name': 'Welcome'})
        self.assertEqual(response.status_code, 302)
        campaign = MailerCampaign.objects.get()
        self.assertContains(self.client.get(response.url), 'Confirm and queue 2 emails')
        self.assertContains(self.client.get('/admin/mailer/'), 'Welcome')
        self.assertContains(self.client.get(f'/admin/mailer/campaign/{campaign.pk}/'), 'center001@example.test')

    def test_directory_sync_keeps_each_center_with_shared_email(self):
        from types import SimpleNamespace
        import json
        rows = [{'entry_key': name, 'payload': json.dumps({'name': name, 'email': email}), 'first_created': stamp}
                for name, email, stamp in [('Older', 'shared@example.test', 1000),
                    ('Newer', 'SHARED@example.test', 2000), ('Invalid', 'not-an-email', 3000)]]
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = rows
        with patch('core.connect') as connect, patch('core.active_batch', return_value='active'):
            connect.return_value.__enter__.return_value = db
            self.assertEqual(admin_mailer.sync_contacts(), 2)
        active = list(MailerContact.objects.filter(active=True))
        self.assertEqual([c.email for c in active], ['shared@example.test', 'shared@example.test'])
        self.assertEqual([c.centers for c in active], [['Newer'], ['Older']])
        self.assertEqual(int(active[0].imported_at.timestamp()), 2000)
        with patch('core.connect') as connect, patch('core.active_batch', return_value='active'):
            connect.return_value.__enter__.return_value = db
            admin_mailer.sync_contacts()
        self.assertEqual(MailerContact.objects.filter(active=True).count(), 2)

    def test_checked_centers_override_range_and_shared_email_sends_separately(self):
        now = timezone.now()
        a = MailerContact.objects.create(center_key='branch-a', email='shared@example.test', centers=['Branch A'], imported_at=now)
        b = MailerContact.objects.create(center_key='branch-b', email='shared@example.test', centers=['Branch B'], imported_at=now)
        with patch('booking_mail.send_message') as send:
            response = self.client.post('/admin/mailer/', {**self.data, 'action': 'preview',
                'selection': '1', 'selected_contacts': [str(a.pk), str(b.pk)]})
            self.assertEqual(response.status_code, 302)
            campaign = MailerCampaign.objects.get()
            self.assertEqual(campaign.messages.count(), 2)
            self.assertEqual(set(campaign.messages.values_list('center_names', flat=True)), {'Branch A', 'Branch B'})
            self.assertEqual(campaign.messages.values('message_id').distinct().count(), 2)
            send.assert_not_called()
            admin_mailer.queue_campaign(campaign.pk)
            admin_mailer.send_pending()
            self.assertEqual(send.call_count, 2)
            for call in send.call_args_list:
                self.assertEqual(call.args[1], 'shared@example.test')
        target = campaign.messages.get(contact=a)
        self.assertTrue(admin_mailer.import_reply(self.reply(target), 'shared-mailbox', '1', 1))
        self.assertEqual(target.replies.count(), 1)
        self.assertEqual(campaign.messages.get(contact=b).replies.count(), 0)
        page = self.client.get('/admin/mailer/')
        self.assertContains(page, 'Select Branch A')
        self.assertContains(page, 'Select Branch B')
        self.assertContains(page, 'form="mailer-compose"')

    def test_checkbox_selection_rejects_inactive_or_unknown_center(self):
        item = MailerContact.objects.first()
        item.active=False
        item.save()
        for ids in ([str(item.pk)], ['99999999']):
            response=self.client.post('/admin/mailer/', {**self.data, 'action':'preview', 'selected_contacts':ids})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(MailerCampaign.objects.count(), 0)

    def test_blank_selection_requires_checkboxes_or_range(self):
        form = admin_mailer.ComposeForm({**self.data, 'selection':''})
        self.assertFalse(form.is_valid())

    def test_email_search_is_case_insensitive_sorted_and_ranges_use_filtered_rows(self):
        old = MailerContact.objects.create(center_key='search-old', email='team@filter.example.test', centers=['Older match'], imported_at=timezone.now()-timedelta(days=2))
        new = MailerContact.objects.create(center_key='search-new', email='hello@filter.example.test', centers=['Newest match'], imported_at=timezone.now())
        response = self.client.get('/admin/mailer/', {'email':'FILTER.example.test'})
        self.assertEqual([row['contact'].pk for row in response.context['rows']], [new.pk, old.pk])
        self.assertContains(response, 'Search email')
        response = self.client.post('/admin/mailer/?email=FILTER.example.test', {**self.data, 'action':'preview', 'selection':'1', 'email_filter':'FILTER.example.test'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(MailerCampaign.objects.get().messages.get().contact_id,new.pk)

    def test_email_search_pagination_preserves_filter_and_empty_results(self):
        response = self.client.get('/admin/mailer/', {'email':'@example.test','page':'2'})
        self.assertEqual(response.context['page'].number,2)
        self.assertContains(response,'email=%40example.test&amp;page=3')
        response = self.client.get('/admin/mailer/', {'email':'missing@example.invalid'})
        self.assertEqual(response.context['total'],0)
        self.assertContains(response,'No centers match this email search.')

    def test_checkbox_cannot_send_outside_filtered_results(self):
        item=MailerContact.objects.first()
        response=self.client.post('/admin/mailer/', {**self.data, 'action':'preview', 'email_filter':'no-match.invalid', 'selected_contacts':[str(item.pk)]})
        self.assertEqual(response.status_code,200)
        self.assertEqual(MailerCampaign.objects.count(),0)

    def test_campaign_email_has_branded_html_and_safe_plain_text(self):
        data={**self.data, 'selection':'1', 'subject':'Hello {{center_name}} <script>bad</script>', 'body':'First line\nSecond line <img src=x onerror=bad()> & welcome {{center_name}}'}
        campaign=admin_mailer.preview_campaign(data, 'mailer-admin')
        admin_mailer.queue_campaign(campaign.pk)
        with patch('booking_mail.send_message') as send:
            admin_mailer.send_pending()
            message, recipient=send.call_args.args
        self.assertEqual(message.get_content_type(),'multipart/alternative')
        plain=message.get_body(preferencelist=('plain',)).get_content()
        html=message.get_body(preferencelist=('html',)).get_content()
        self.assertIn('MySelfPay by One Smarter, Inc.',plain)
        self.assertIn('A message from MySelfPay',html)
        self.assertIn('background:#a71930',html)
        self.assertIn('Hello Synthetic center 1,',html)
        self.assertIn('First line<br>',html)
        self.assertNotIn('<script>bad</script>',html)
        self.assertNotIn('<img src=x',html)
        self.assertIn('&lt;img',html)
        self.assertIn('mailto:patientcare@onesmarterhealthweb.com',html)
        self.assertEqual(message['Reply-To'],'patientcare@onesmarterhealthweb.com')
        response=self.client.get(f'/admin/mailer/campaign/{campaign.pk}/')
        self.assertContains(response,'A message from MySelfPay')
        self.assertNotContains(response,'<script>bad</script>')
