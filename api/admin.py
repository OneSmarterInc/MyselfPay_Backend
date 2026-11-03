import re
import secrets
from datetime import datetime, timezone
from django import forms
from django.contrib import admin
from django.contrib.admin import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import connection
from django.urls import reverse
from .models import PortalAccount, PortalMember, PortalSetting, TwilioConfiguration
from admin_settings import secret_cipher
import accounts


class SelfPayAdminSite(AdminSite):
    site_header = 'MySelfPay administration'
    site_title = 'MySelfPay Admin'
    index_title = 'Dashboard'
    index_template = 'admin/selfpay_index.html'
    login_template = 'admin/login.html'
    enable_nav_sidebar = False

    def has_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def index(self, request, extra_context=None):
        context = {'registered_users': PortalAccount.objects.count(),
                   'suspended_users': PortalSetting.objects.filter(key__startswith='account-disabled:', value='1').count(),
                   'twilio_saved': TwilioConfiguration.objects.filter(pk=1).exists()}
        context['active_users'] = max(0, context['registered_users'] - context['suspended_users'])
        recent = list(PortalAccount.objects.order_by('-created')[:6])
        names = dict(PortalMember.objects.filter(pk__in=[a.pk for a in recent]).values_list('pk', 'name'))
        suspended = set(PortalSetting.objects.filter(key__in=['account-disabled:' + a.pk for a in recent], value='1').values_list('key', flat=True))
        context['recent_accounts'] = [{'name': names.get(a.pk, 'Portal user'), 'email': a.email,
                                     'registered': datetime.fromtimestamp(a.created, timezone.utc).strftime('%d %b %Y'),
                                     'suspended': 'account-disabled:' + a.pk in suspended,
                                     'url': reverse('admin:api_portalaccount_change', args=[a.pk])} for a in recent]
        context.update(extra_context or {})
        return super().index(request, context)


admin_site = SelfPayAdminSite(name='admin')
admin_site.register(get_user_model(), UserAdmin)


class PortalAccountForm(forms.ModelForm):
    name = forms.CharField(max_length=100)
    active = forms.BooleanField(required=False, initial=True, help_text='Uncheck to block login and revoke portal sessions.')
    new_password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
                                  help_text='Leave blank to keep the current password. At least 12 characters.')

    class Meta:
        model = PortalAccount
        fields = ('name', 'email', 'phone', 'active', 'new_password')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            member = PortalMember.objects.get(pk=self.instance.member_id)
            self.fields['name'].initial = member.name
            self.fields['active'].initial = not PortalSetting.objects.filter(key='account-disabled:' + member.pk, value='1').exists()

    def clean_email(self):
        try:
            email = accounts.email_address(self.cleaned_data['email'])
        except accounts.AccountError as exc:
            raise ValidationError(str(exc)) from None
        if PortalMember.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise ValidationError('An account already uses this email address.')
        return email

    def clean_phone(self):
        value = self.cleaned_data.get('phone', '').strip()
        if value and not re.fullmatch(r'[+0-9 ()\-.]{7,30}', value):
            raise ValidationError('Enter a valid phone number or leave it blank.')
        return value

    def clean_new_password(self):
        password = self.cleaned_data.get('new_password', '')
        if password:
            validate_password(password)
            if len(password) > 128:
                raise ValidationError('Use at most 128 characters.')
        return password


class PortalAccountAdmin(admin.ModelAdmin):
    form = PortalAccountForm
    list_display = ('email', 'user_name', 'phone', 'registered_at', 'account_status')
    search_fields = ('email', 'phone', 'member_id')
    ordering = ('-created',)
    list_per_page = 50
    fields = ('name', 'email', 'phone', 'active', 'new_password', 'registered_at', 'member_id')
    readonly_fields = ('registered_at', 'member_id')
    actions = ('suspend_accounts', 'restore_accounts')

    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return False

    @admin.display(description='Name')
    def user_name(self, obj):
        return PortalMember.objects.get(pk=obj.member_id).name

    @admin.display(description='Registered', ordering='created')
    def registered_at(self, obj):
        return datetime.fromtimestamp(obj.created, timezone.utc).strftime('%d %b %Y, %H:%M UTC')

    @admin.display(description='Status')
    def account_status(self, obj):
        return 'Suspended' if PortalSetting.objects.filter(key='account-disabled:' + obj.pk, value='1').exists() else 'Active'

    def revoke_sessions(self, member_id):
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM sessions WHERE member_id=%s', [member_id])

    def save_model(self, request, obj, form, change):
        member = PortalMember.objects.select_for_update().get(pk=obj.member_id)
        changed_email = member.email != obj.email
        member.name = form.cleaned_data['name']
        member.email = obj.email
        password = form.cleaned_data.get('new_password')
        if password:
            member.salt = secrets.token_hex(16)
            member.password_hash = 'scrypt-v1$' + accounts.password_hash(password, member.salt)
        member.save(update_fields=['name', 'email', 'salt', 'password_hash'])
        obj.save()
        active = form.cleaned_data['active']
        PortalSetting.objects.update_or_create(key='account-disabled:' + obj.pk, defaults={'value': '0' if active else '1'})
        if not active or password or changed_email:
            self.revoke_sessions(obj.pk)

    @admin.action(description='Suspend selected users and revoke their sessions')
    def suspend_accounts(self, request, queryset):
        for obj in queryset:
            PortalSetting.objects.update_or_create(key='account-disabled:' + obj.pk, defaults={'value': '1'})
            self.revoke_sessions(obj.pk)
        self.message_user(request, 'Selected users suspended.')

    @admin.action(description='Restore selected users')
    def restore_accounts(self, request, queryset):
        for obj in queryset:
            PortalSetting.objects.update_or_create(key='account-disabled:' + obj.pk, defaults={'value': '0'})
        self.message_user(request, 'Selected users restored.')


class TwilioConfigurationForm(forms.ModelForm):
    api_key_secret = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
                                    help_text='Write-only. Leave blank to keep the saved secret.')
    clear_secret = forms.BooleanField(required=False, help_text='Delete the saved secret. Environment fallback may still apply.')

    class Meta:
        model = TwilioConfiguration
        fields = ('api_key_sid', 'api_key_secret', 'account_sid', 'verify_service_sid', 'clear_secret')

    def clean(self):
        data = super().clean()
        for field, prefix in [('api_key_sid', 'SK'), ('account_sid', 'AC'), ('verify_service_sid', 'VA')]:
            value = data.get(field, '').strip()
            if value and not re.fullmatch(prefix + r'[0-9a-fA-F]{32}', value):
                self.add_error(field, 'Use a valid ' + prefix + ' SID (34 characters).')
            data[field] = value
        secret = data.get('api_key_secret', '')
        if secret and (len(secret) > 512 or any(ord(c) < 33 for c in secret)):
            self.add_error('api_key_secret', 'Enter the API secret without spaces or line breaks.')
        return data


class TwilioConfigurationAdmin(admin.ModelAdmin):
    form = TwilioConfigurationForm
    fields = ('api_key_sid', 'api_key_secret', 'secret_status', 'account_sid', 'verify_service_sid', 'clear_secret', 'updated_at', 'updated_by')
    readonly_fields = ('secret_status', 'updated_at', 'updated_by')
    list_display = ('__str__', 'secret_status', 'updated_at', 'updated_by')

    def has_add_permission(self, request):
        return not TwilioConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None): return False

    @admin.display(description='Saved secret')
    def secret_status(self, obj):
        return 'Configured (hidden)' if obj and obj.secret_encrypted else 'Not saved'

    def save_model(self, request, obj, form, change):
        obj.pk = 1
        if form.cleaned_data.get('clear_secret'):
            obj.secret_encrypted = ''
        elif form.cleaned_data.get('api_key_secret'):
            obj.secret_encrypted = secret_cipher().encrypt(form.cleaned_data['api_key_secret'].encode()).decode()
        obj.updated_by = request.user.get_username()
        super().save_model(request, obj, form, change)


admin_site.register(PortalAccount, PortalAccountAdmin)
admin_site.register(TwilioConfiguration, TwilioConfigurationAdmin)
