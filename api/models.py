import uuid
from django.db import models


class SpatialLocation(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    batch_id = models.TextField()
    entry_key = models.TextField()
    ordinal = models.IntegerField()
    h3_res7 = models.CharField(max_length=15)
    h3_res11 = models.CharField(max_length=15)
    location = models.TextField()

    class Meta:
        managed = False
        db_table = 'spatial_locations'
        indexes = [models.Index(fields=['batch_id', 'h3_res7'], name='spatial_batch_res7')]


class PortalAccount(models.Model):
    member_id = models.CharField(primary_key=True, max_length=100)
    email = models.EmailField(max_length=254)
    phone = models.CharField(max_length=30, blank=True)
    created = models.FloatField()

    class Meta:
        managed = False
        db_table = 'user_accounts'
        verbose_name = 'registered portal user'
        verbose_name_plural = 'registered portal users'

    def __str__(self):
        return self.phone or self.member_id


class PortalMember(models.Model):
    id = models.CharField(primary_key=True, max_length=100)
    tenant = models.CharField(max_length=100)
    email = models.EmailField(max_length=254)
    name = models.CharField(max_length=100)
    salt = models.TextField()
    password_hash = models.TextField()

    class Meta:
        managed = False
        db_table = 'members'


class PortalSetting(models.Model):
    key = models.CharField(primary_key=True, max_length=200)
    value = models.TextField()

    class Meta:
        managed = False
        db_table = 'settings'


class TwilioConfiguration(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    api_key_sid = models.CharField(max_length=34, blank=True, verbose_name='API Key SID (SK)')
    account_sid = models.CharField(max_length=34, blank=True, verbose_name='Account SID (AC)')
    verify_service_sid = models.CharField(max_length=34, blank=True, verbose_name='Verify Service SID (VA)')
    secret_encrypted = models.TextField(blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=150, blank=True, editable=False)

    class Meta:
        verbose_name = 'Twilio configuration'
        verbose_name_plural = 'Twilio configuration'

    def __str__(self):
        return 'Twilio SMS verification settings'


class SMTPConfiguration(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    use_database = models.BooleanField(default=False, verbose_name='Use saved SMTP settings', help_text='When off, the server environment remains authoritative.')
    host = models.CharField(max_length=253, blank=True)
    port = models.PositiveIntegerField(default=587)
    username = models.CharField(max_length=254, blank=True)
    password_encrypted = models.TextField(blank=True, editable=False)
    from_email = models.EmailField(blank=True, verbose_name='Sender email')
    from_name = models.CharField(max_length=100, default='MySelfPay', verbose_name='Sender name')
    security = models.CharField(max_length=10, choices=[('starttls', 'STARTTLS (usually port 587)'), ('ssl', 'Implicit TLS (usually port 465)')], default='starttls')
    booking_email_enabled = models.BooleanField(default=False, verbose_name='Enable direct booking emails', help_text='Secure Mail sends its own invitations when that integration is enabled.')
    allowed_recipients = models.TextField(blank=True, help_text='Optional comma-separated center emails for testing. Blank allows all center recipients.')
    test_recipient = models.EmailField(blank=True, help_text='Recipient used by the check_smtp --send command.')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=150, blank=True, editable=False)

    class Meta:
        verbose_name = 'SMTP email configuration'
        verbose_name_plural = 'SMTP email configuration'

    def __str__(self):
        return 'MySelfPay email settings'


class MailerMailbox(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=False)
    host = models.CharField(max_length=253, default='imap.ionos.com')
    port = models.PositiveIntegerField(default=993)
    username = models.EmailField(blank=True, default='patientcare@onesmarterhealthweb.com')
    password_encrypted = models.TextField(blank=True, editable=False)
    folder = models.CharField(max_length=100, default='INBOX')
    reply_to = models.EmailField(blank=True, default='patientcare@onesmarterhealthweb.com')
    cursor_identity = models.CharField(max_length=64, blank=True, editable=False)
    uid_validity = models.CharField(max_length=100, blank=True, editable=False)
    last_uid = models.PositiveBigIntegerField(default=0, editable=False)
    last_sync = models.DateTimeField(null=True, editable=False)
    sync_status = models.CharField(max_length=200, blank=True, editable=False)
    sync_requested = models.BooleanField(default=False, editable=False)


class MailerContact(models.Model):
    center_key = models.CharField(max_length=300, blank=True, default="")
    email = models.EmailField()
    centers = models.JSONField(default=list)
    imported_at = models.DateTimeField(db_index=True)
    active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ['-imported_at', 'center_key', 'email', 'pk']
        constraints = [models.UniqueConstraint(fields=['center_key', 'email'], name='mailer_center_email_unique')]


class MailerTemplate(models.Model):
    name = models.CharField(max_length=100, unique=True)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)


class MailerCampaign(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    selection = models.CharField(max_length=40)
    reply_to = models.EmailField()
    created_by = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)
    queued_at = models.DateTimeField(null=True)
    state = models.CharField(max_length=20, default='draft', db_index=True)


class MailerMessage(models.Model):
    campaign = models.ForeignKey(MailerCampaign, on_delete=models.CASCADE, related_name='messages')
    contact = models.ForeignKey(MailerContact, on_delete=models.PROTECT, related_name='outbound')
    email = models.EmailField()
    center_names = models.TextField()
    position = models.PositiveIntegerField()
    message_id = models.CharField(max_length=254, unique=True)
    state = models.CharField(max_length=20, default='draft', db_index=True)
    sent_at = models.DateTimeField(null=True)
    attempted_at = models.DateTimeField(null=True)
    error = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['campaign', 'contact'], name='mailer_campaign_contact_unique')]


class MailerReply(models.Model):
    message = models.ForeignKey(MailerMessage, on_delete=models.CASCADE, related_name='replies')
    identity = models.CharField(max_length=64, unique=True)
    sender = models.EmailField()
    subject = models.CharField(max_length=500)
    body_encrypted = models.TextField()
    received_at = models.DateTimeField()


class MailerLease(models.Model):
    name = models.CharField(max_length=40, primary_key=True)
    token = models.CharField(max_length=64, blank=True)
    expires = models.DateTimeField()
