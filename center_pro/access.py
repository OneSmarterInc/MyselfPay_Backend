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
