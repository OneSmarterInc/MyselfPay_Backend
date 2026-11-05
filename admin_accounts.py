"""Transactional deletion of registered portal accounts, never Django admins."""
import hashlib
from django.db import connection, transaction


def delete_registered_user(member_id):
    owner = hashlib.sha256(member_id.encode()).hexdigest()
    with transaction.atomic(), connection.cursor() as cursor:
        lock = ' FOR UPDATE' if connection.vendor == 'postgresql' else ''
        cursor.execute("SELECT m.id FROM members m JOIN user_accounts a ON a.member_id=m.id WHERE m.id=%s AND m.tenant='selfpay'" + lock, [member_id])
        if not cursor.fetchone():
            raise ValueError('Registered user unavailable.')
        tables = set(connection.introspection.table_names(cursor))
        # All known booking children have ON DELETE CASCADE. Their capabilities
        # and queued notifications disappear in the same transaction.
        for table in ('booking_requests', 'booking_email_checks', 'booking_phone_checks', 'booking_verified_emails'):
            if table in tables:
                cursor.execute(f'DELETE FROM {table} WHERE owner_hash=%s', [owner])
        if 'mobile_accounts' in tables and 'booking_phone_checks' in tables:
            cursor.execute('SELECT phone FROM mobile_accounts WHERE member_id=%s', [member_id])
            row = cursor.fetchone()
            if row:
                cursor.execute('DELETE FROM booking_phone_checks WHERE phone_hash=%s', [hashlib.sha256(row[0].encode()).hexdigest()])
        for table in ('guest_usage', 'sessions', 'conversations', 'preferences', 'corrections', 'account_zip_codes', 'mobile_accounts', 'user_accounts'):
            if table in tables:
                cursor.execute(f'DELETE FROM {table} WHERE member_id=%s', [member_id])
        for key in ('account-disabled:' + member_id, 'booking-phone-owner:' + owner, 'booking-email-owner:' + owner):
            cursor.execute('DELETE FROM settings WHERE key=%s', [key])
        cursor.execute('DELETE FROM members WHERE id=%s', [member_id])
