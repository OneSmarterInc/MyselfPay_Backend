import secrets
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from .models import Center,Membership,AuditEvent
from .services import Conflict

@transaction.atomic
def assign_access(actor,center_id,data):
    center=get_object_or_404(Center.objects.select_for_update(),pk=center_id,directory_active=True)
    matches=get_user_model().objects.filter(email__iexact=data['email'])
    if matches.count()>1:raise ValidationError('Multiple accounts use this email. Resolve identity mapping first.')
    user=matches.first()
    if user and not user.is_active:raise ValidationError('This account is inactive. Activate it before assigning center access.')
    if not user:
        user=get_user_model().objects.create_user(username='center-'+secrets.token_hex(12),email=data['email'].lower(),first_name=data['name'])
        user.set_unusable_password();user.save()
    if Membership.objects.filter(center=center,user=user).exists():raise Conflict('This person already has access. Revoke their assignment before changing their role.')
    membership=Membership.objects.create(center=center,user=user,role=data['role'])
    AuditEvent.objects.create(center=center,actor=actor,action='access.assigned',reference=f'MEMBER-{membership.id}')
    return membership

@transaction.atomic
def revoke_access(actor,center_id,member_id):
    center=get_object_or_404(Center.objects.select_for_update(),pk=center_id,directory_active=True)
    member=get_object_or_404(Membership,pk=member_id,center=center)
    if member.role=='owner' and Membership.objects.filter(center=center,role='owner').count()==1:
        raise Conflict('Assign another owner before removing the last owner.')
    member.delete();AuditEvent.objects.create(center=center,actor=actor,action='access.revoked',reference=f'MEMBER-{member_id}')


@transaction.atomic
def directory_owner_access(email):
    """Provision default owner access only for an exact active directory mailbox match.

    Login still requires a single-use emailed link. Explicit assignments are never
    changed, inactive/ambiguous accounts are not reused, and admin revocations win.
    """
    import json
    import core, booking
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError as InvalidEmail
    from .integration import DomainConnection
    import center_portal
    email=str(email).strip().lower()
    try:validate_email(email)
    except InvalidEmail:return None
    db=DomainConnection()
    listings=center_portal.centers(db,email)
    if not listings:return None
    users=get_user_model().objects.filter(email__iexact=email)
    if users.count()>1:return None
    user=users.first()
    if user and not user.is_active:return None
    # A revoked center remains manual-only, including revocations made before this feature.
    eligible=[entry for entry in listings if not AuditEvent.objects.filter(center__directory_key=entry['id'],action='access.revoked').exists()]
    if not eligible:return user
    # Serialize first-login provisioning for the same mailbox across concurrent requests.
    import hashlib
    from django.utils import timezone
    from .models import AuthLimit
    lock,_=AuthLimit.objects.get_or_create(key=hashlib.sha256(('directory-owner:'+email).encode()).hexdigest(),defaults={'resets_at':timezone.now()})
    AuthLimit.objects.select_for_update().get(pk=lock.pk)
    users=get_user_model().objects.filter(email__iexact=email)
    if users.count()>1:return None
    user=users.first()
    if user and not user.is_active:return None
    if not user:
        user=get_user_model().objects.create_user(username='center-'+secrets.token_hex(12),email=email)
        user.set_unusable_password();user.save(update_fields=['password'])
    batch=core.active_batch(db)
    for entry in sorted(eligible,key=lambda e:e['id']):
        row=db.execute('SELECT payload FROM entries WHERE batch_id=? AND entry_key=?',(batch,entry['id'])).fetchone()
        if not row:continue
        payload=json.loads(row['payload'])
        center,_=Center.objects.get_or_create(directory_key=entry['id'],defaults={'name':entry['name'][:180],'timezone':booking.default_timezone(payload),'directory_active':True})
        center=Center.objects.select_for_update().get(pk=center.pk)
        if not center.directory_active or AuditEvent.objects.filter(center=center,action='access.revoked').exists():continue
        member,created=Membership.objects.get_or_create(center=center,user=user,defaults={'role':'owner'})
        if created:AuditEvent.objects.create(center=center,actor=user,action='access.directory_owner',reference=f'MEMBER-{member.pk}')
    return user


def _owned_center(actor,center_id):
    center=get_object_or_404(Center.objects.select_for_update(),pk=center_id,directory_active=True)
    get_object_or_404(Membership,center=center,user=actor,role='owner')
    return center


@transaction.atomic
def assign_staff(actor,center_id,data):
    # Recheck ownership while holding the same center lock as access mutations.
    _owned_center(actor,center_id)
    return assign_access(actor,center_id,{**data,'role':'staff'})


@transaction.atomic
def revoke_staff(actor,center_id,member_id):
    center=_owned_center(actor,center_id)
    get_object_or_404(Membership,pk=member_id,center=center,role='staff')
    revoke_access(actor,center_id,member_id)
