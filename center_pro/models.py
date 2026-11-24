from django.conf import settings
from django.db import models

class Center(models.Model):
    directory_key = models.CharField(max_length=200, unique=True, null=True, blank=True)
    directory_active = models.BooleanField(default=True)
    name = models.CharField(max_length=180)
    timezone = models.CharField(max_length=64, default='America/New_York')

class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = 'owner', 'Owner'
        STAFF = 'staff', 'Staff'
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    center = models.ForeignKey(Center, on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=Role.choices)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'center'], name='pro_unique_membership')]

class Slot(models.Model):
    center = models.ForeignKey(Center, on_delete=models.CASCADE)
    resource = models.CharField(max_length=80)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    blocked = models.BooleanField(default=False)
    class Meta:
        ordering = ['starts_at', 'id']
        constraints = [models.UniqueConstraint(fields=['center', 'resource', 'starts_at'], name='pro_unique_slot_start'), models.CheckConstraint(condition=models.Q(ends_at__gt=models.F('starts_at')), name='pro_positive_slot_duration')]

class BookingRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Needs scheduling'
        CONFIRMED = 'confirmed', 'Confirmed'
        CLOSED = 'closed', 'Closed'
    center = models.ForeignKey(Center, on_delete=models.CASCADE)
    legacy_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    reference = models.CharField(max_length=32, unique=True)
    patient_name = models.CharField(max_length=120)
    exam = models.CharField(max_length=180)
    source = models.CharField(max_length=30, default='myselfpay')
    preferred_windows = models.JSONField(default=list)
    preferred_slot_ids = models.JSONField(default=list)
    price_snapshot = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    slot = models.OneToOneField(Slot, null=True, blank=True, on_delete=models.PROTECT, related_name='booking')
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class AuditEvent(models.Model):
    center = models.ForeignKey(Center, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=80)
    reference = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)



class Offer(models.Model):
    directory_offer_id = models.CharField(max_length=200, unique=True, null=True, blank=True)
    center = models.ForeignKey(Center, on_delete=models.CASCADE)
    exam = models.CharField(max_length=180)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    benchmark_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    benchmark_source = models.CharField(max_length=180, blank=True)
    benchmark_checked_at = models.DateField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['center', 'exam'], name='pro_unique_offer_exam'), models.CheckConstraint(condition=models.Q(price__gt=0), name='pro_positive_offer_price')]


class PriceChange(models.Model):
    offer = models.ForeignKey(Offer, on_delete=models.PROTECT, related_name='changes')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    old_price = models.DecimalField(max_digits=10, decimal_places=2)
    new_price = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)


class OutreachTarget(models.Model):
    center = models.ForeignKey(Center, on_delete=models.CASCADE)
    name = models.CharField(max_length=180)
    category = models.CharField(max_length=80)
    distance_miles = models.DecimalField(max_digits=6, decimal_places=1)
    status = models.CharField(max_length=30, default='not_contacted')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['center', 'name'], name='pro_unique_outreach_target')]


class OutreachActivity(models.Model):
    target = models.ForeignKey(OutreachTarget, on_delete=models.PROTECT, related_name='activities')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=30, choices=[('visited', 'Visit completed'), ('follow_up', 'Follow-up planned'), ('rate_sheet_shared', 'Rate sheet shared externally')])
    note = models.CharField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)



class MagicLink(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True)


class AuthLimit(models.Model):
    key = models.CharField(max_length=64, unique=True)
    count = models.PositiveIntegerField(default=0)
    resets_at = models.DateTimeField()


class AdminMFA(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    encrypted_secret = models.TextField()
    last_counter = models.BigIntegerField(default=-1)


class WorkingHours(models.Model):
    center = models.ForeignKey(Center, on_delete=models.CASCADE)
    weekday = models.PositiveSmallIntegerField()
    opens = models.TimeField()
    closes = models.TimeField()
    closed = models.BooleanField(default=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['center','weekday'],name='pro_unique_weekday'), models.CheckConstraint(condition=models.Q(weekday__lte=6),name='pro_valid_weekday')]


class PatientLink(models.Model):
    center = models.ForeignKey(Center, on_delete=models.CASCADE)
    offer = models.ForeignKey(Offer, on_delete=models.PROTECT)
    digest = models.CharField(max_length=64, unique=True)
    price_snapshot = models.DecimalField(max_digits=10, decimal_places=2)
    expires_at = models.DateTimeField()
    consent = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    booking = models.OneToOneField(BookingRequest, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class EstimateDraft(models.Model):
    booking = models.ForeignKey(BookingRequest, on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['booking','version'],name='pro_unique_estimate_version')]


class EmployerDirectory(models.Model):
    source_id=models.CharField(max_length=80,unique=True)
    operator_name=models.TextField()
    operator_address=models.TextField(blank=True)
    operator_location=models.TextField(blank=True)
    center=models.ForeignKey(Center,null=True,blank=True,on_delete=models.SET_NULL,related_name='employer_directories')

class NearbyCompany(models.Model):
    directory=models.ForeignKey(EmployerDirectory,on_delete=models.CASCADE,related_name='companies')
    source_row=models.PositiveIntegerField()
    company_name=models.TextField()
    company_address=models.TextField(blank=True)
    public_email=models.TextField(blank=True)
    email_type=models.TextField(blank=True)
    business_phone=models.TextField(blank=True)
    employee_count=models.TextField(blank=True)
    distance_miles=models.DecimalField(max_digits=12,decimal_places=4,null=True,blank=True)
    distance_text=models.TextField(blank=True)
    location_distance_status=models.TextField(blank=True)
    verification_status=models.TextField(blank=True)
    source_links=models.TextField(blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['directory','source_row'],name='pro_company_source_row')]
        indexes=[models.Index(fields=['directory','distance_miles'],name='pro_company_distance')]


class PreviousScanRecord(models.Model):
    source_id=models.CharField(max_length=80,unique=True)
    center=models.ForeignKey(Center,null=True,blank=True,on_delete=models.SET_NULL,related_name='previous_zip_scans')
    center_name=models.TextField()
    center_location=models.TextField(blank=True)
    zip_code=models.CharField(max_length=5,blank=True,db_index=True)
    scans_2022=models.PositiveBigIntegerField(null=True,blank=True)
    scans_2023=models.PositiveBigIntegerField(null=True,blank=True)
    scans_2024=models.PositiveBigIntegerField(null=True,blank=True)
    source_status=models.TextField(blank=True)
    source_note=models.TextField(blank=True)
