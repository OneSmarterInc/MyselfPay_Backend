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
from django.urls import reverse, path
from django.template.response import TemplateResponse
from django.http import Http404
import admin_bookings
import admin_accounts
import booking
import sqlite3
import psycopg
from .models import PortalAccount, PortalMember, PortalSetting, TwilioConfiguration, SMTPConfiguration
from admin_settings import secret_cipher
import accounts
import core
import mobile_accounts
from django.conf import settings


class SelfPayAdminSite(AdminSite):
    site_header = 'MySelfPay administration'
    site_title = 'MySelfPay Admin'
    index_title = 'Dashboard'
    index_template = 'admin/selfpay_index.html'
    login_template = 'admin/login.html'
    enable_nav_sidebar = False

    def has_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def get_urls(self):
        import admin_mailer
        return [path('mailer/', self.admin_view(lambda request: admin_mailer.mailer_view(self, request)), name='mailer'),
                path('mailer/campaign/<uuid:ident>/', self.admin_view(lambda request, ident: admin_mailer.campaign_view(self, request, ident)), name='mailer_campaign'),
                path('mailer/message/<int:ident>/', self.admin_view(lambda request, ident: admin_mailer.message_view(self, request, ident)), name='mailer_message'),
                path('requests/',self.admin_view(self.requests_view),name='booking_requests'),
                path('requests/<str:ident>/',self.admin_view(self.request_view),name='booking_request')] + super().get_urls()

    def requests_view(self, request):
        mode='approved' if request.GET.get('filter')=='approved' else 'all'
        try: number=int(request.GET.get('page','1'))
        except ValueError: number=1
        context={**self.each_context(request),'title':'Appointment requests',**admin_bookings.page(number,mode)}
        return TemplateResponse(request,'admin/booking_requests.html',context)

    def request_view(self, request, ident):
        try: item=admin_bookings.detail(ident)
        except booking.BookingError: raise Http404('Request unavailable') from None
        return TemplateResponse(request,'admin/booking_request.html',{**self.each_context(request),'title':'Appointment request','item':item})

    def index(self, request, extra_context=None):
        context = {'registered_users': PortalAccount.objects.count(),
                   'suspended_users': PortalSetting.objects.filter(key__startswith='account-disabled:', value='1').count(),
                   'twilio_saved': TwilioConfiguration.objects.filter(pk=1).exists()}
        try:
            context.update(admin_bookings.stats())
        except (sqlite3.DatabaseError, psycopg.Error):
            context.update(request_total='Unavailable',request_approved='Unavailable')
        context['active_users'] = max(0, context['registered_users'] - context['suspended_users'])
        recent = list(PortalAccount.objects.order_by('-created')[:6])
        names = dict(PortalMember.objects.filter(pk__in=[a.pk for a in recent]).values_list('pk', 'name'))
        suspended = set(PortalSetting.objects.filter(key__in=['account-disabled:' + a.pk for a in recent], value='1').values_list('key', flat=True))
        context['recent_accounts'] = [{'name': names.get(a.pk, 'Portal user'), 'phone': a.phone,
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
        fields = ('name', 'phone', 'active', 'new_password')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            member = PortalMember.objects.get(pk=self.instance.member_id)
            self.fields['name'].initial = member.name
            self.fields['active'].initial = not PortalSetting.objects.filter(key='account-disabled:' + member.pk, value='1').exists()

    def clean_phone(self):
        value = self.cleaned_data.get('phone', '').strip()
        if value and not re.fullmatch(r'[+0-9 ()\-.]{7,30}', value):
            raise ValidationError('Enter a valid phone number or leave it blank.')
        with core.connect(settings.DOMAIN_DATABASE) as db:
            row = db.execute('SELECT phone FROM mobile_accounts WHERE member_id=?', (self.instance.pk,)).fetchone()
        if row and value != row['phone']:
            raise ValidationError('A verified mobile number cannot be edited without verifying the replacement. Contact support for account recovery.')
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
    list_display = ('username', 'user_name', 'phone', 'registered_at', 'account_status')
    search_fields = ('phone', 'member_id')
    ordering = ('-created',)
    list_per_page = 50
    fields = ('name', 'username', 'phone', 'active', 'new_password', 'registered_at', 'member_id')
    readonly_fields = ('registered_at', 'member_id', 'username')
    actions = ('suspend_accounts', 'restore_accounts')

    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_superuser)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)  # Confirm one account at a time.
        return actions

    def get_deleted_objects(self, objs, request):
        deleted, counts, permissions, protected = super().get_deleted_objects(objs, request)
        deleted.append('Their profile, verified mobile identity, sessions, saved searches and owned appointment requests. Center notifications and private request links will also be removed. This cannot be undone.')
        return deleted, counts, permissions, protected

    def delete_model(self, request, obj):
        admin_accounts.delete_registered_user(obj.member_id)


    @admin.display(description='Username')
    def username(self, obj):
        with core.connect(settings.DOMAIN_DATABASE) as db:
            row = db.execute('SELECT username FROM mobile_accounts WHERE member_id=?', (obj.pk,)).fetchone()
        return row['username'] if row else 'Setup required'

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
        member.name = form.cleaned_data['name']
        password = form.cleaned_data.get('new_password')
        if password:
            member.salt = secrets.token_hex(16)
            member.password_hash = 'scrypt-v1$' + accounts.password_hash(password, member.salt)
        member.save(update_fields=['name', 'salt', 'password_hash'])
        obj.save()
        active = form.cleaned_data['active']
        PortalSetting.objects.update_or_create(key='account-disabled:' + obj.pk, defaults={'value': '0' if active else '1'})
        if not active or password:
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


class SMTPConfigurationForm(forms.ModelForm):
    password = forms.CharField(required=False, strip=False, widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}), help_text='Write-only. Leave blank to keep the saved password. Use your provider’s SMTP password or app password.')

    class Meta:
        model = SMTPConfiguration
        fields = ('use_database', 'host', 'port', 'username', 'password', 'from_email', 'from_name', 'security', 'booking_email_enabled', 'allowed_recipients', 'test_recipient')

    def clean(self):
        data = super().clean()
        if not 1 <= (data.get('port') or 0) <= 65535:
            self.add_error('port', 'Enter a port between 1 and 65535.')
        for field in ('host', 'username', 'from_name'):
            if any(ord(c) < 32 for c in data.get(field, '')):
                self.add_error(field, 'Remove line breaks and control characters.')
        host = data.get('host', '')
        if host and (any(c.isspace() for c in host) or '://' in host or '/' in host or '@' in host):
            self.add_error('host', 'Enter the SMTP hostname only, for example smtp.gmail.com.')
        password = data.get('password', '')
        if password and (len(password) > 4096 or any(c in password for c in '\r\n\0')):
            self.add_error('password', 'Enter the password without line breaks; maximum 4096 characters.')
        addresses = [value.strip() for value in data.get('allowed_recipients', '').split(',') if value.strip()]
        for address in addresses:
            try:
                forms.EmailField().clean(address)
            except ValidationError:
                self.add_error('allowed_recipients', 'Use valid email addresses separated by commas.')
                break
        data['allowed_recipients'] = ','.join(addresses)
        if data.get('use_database'):
            for field in ('host', 'username', 'from_email'):
                if not data.get(field):
                    self.add_error(field, 'Required when using saved SMTP settings.')
            if not password and not self.instance.password_encrypted:
                self.add_error('password', 'Enter a password before enabling saved SMTP settings.')
        return data


class SMTPConfigurationAdmin(admin.ModelAdmin):
    form = SMTPConfigurationForm
    readonly_fields = ('password_status', 'updated_at', 'updated_by')
    list_display = ('__str__', 'use_database', 'host', 'password_status', 'updated_at', 'updated_by')
    fieldsets = (
        ('Configuration source', {'fields': ('use_database',), 'description': 'Saved settings take effect on the next email. Turn this off to use the server environment. Secure Mail has a separate sending configuration.'}),
        ('SMTP connection', {'fields': ('host', 'port', 'security', 'username', 'password', 'password_status')}),
        ('Sender identity', {'fields': ('from_email', 'from_name')}),
        ('Booking delivery', {'fields': ('booking_email_enabled', 'allowed_recipients', 'test_recipient'), 'description': 'These controls affect direct MySelfPay emails. Secure Mail invitations are sent by the Secure Mail service.'}),
        ('Last updated', {'fields': ('updated_at', 'updated_by')}),
    )

    def has_add_permission(self, request):
        return not SMTPConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def render_change_form(self, request, context, *args, **kwargs):
        context['show_save_and_add_another'] = False
        return super().render_change_form(request, context, *args, **kwargs)

    @admin.display(description='Saved password')
    def password_status(self, obj):
        return 'Configured (hidden)' if obj and obj.password_encrypted else 'Not saved'

    def save_model(self, request, obj, form, change):
        obj.pk = 1
        if form.cleaned_data.get('password'):
            obj.password_encrypted = secret_cipher('smtp').encrypt(form.cleaned_data['password'].encode()).decode()
        obj.updated_by = request.user.get_username()
        super().save_model(request, obj, form, change)


admin_site.register(SMTPConfiguration, SMTPConfigurationAdmin)
