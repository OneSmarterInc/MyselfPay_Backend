"""Paginated center history from existing workspace and portal records."""
from datetime import datetime, timezone
from django.db import connection
from django.contrib.auth import get_user_model
from .integration import DomainConnection


class CenterActivityRows:
    def __init__(self, center):
        self.db=DomainConnection()
        epoch="(julianday(created_at)-2440587.5)*86400.0" if connection.vendor=='sqlite' else 'EXTRACT(EPOCH FROM created_at)::double precision'
        user_table=connection.ops.quote_name(get_user_model()._meta.db_table)
        parts=[f"SELECT 'event-' || CAST(a.id AS TEXT) AS id,a.action,a.reference,{epoch.replace('created_at','a.created_at')} AS happened,CASE WHEN a.action='access.directory_owner' THEN 'System' WHEN u.is_superuser THEN 'Administrator' ELSE 'Center team' END AS performed_by,'' AS field,'' AS old_value,'' AS new_value FROM center_pro_auditevent a LEFT JOIN {user_table} u ON u.id=a.actor_id WHERE a.center_id=?"]
        self.params=[center.pk]
        if center.directory_key:
            key=center.directory_key
            parts.extend([
                "SELECT 'profile-submit-' || id,'profile.submitted','PROFILE-' || id,submitted,'Center team',field,COALESCE(old_value,''),value FROM center_profile_edits WHERE entry_key=?",
                "SELECT 'profile-decision-' || id,'profile.' || status,'PROFILE-' || id,decided,'Administrator',field,COALESCE(old_value,''),value FROM center_profile_edits WHERE entry_key=? AND status IN ('approved','rejected') AND decided IS NOT NULL",
                "SELECT 'price-submit-' || id,'price.submitted','PRICE-' || id,submitted,'Center team',exam,'',CAST(amount_cents AS TEXT) FROM center_price_submissions WHERE entry_key=?",
                "SELECT 'price-decision-' || id,'price.' || status,'PRICE-' || id,decided,'Administrator',exam,'',CAST(amount_cents AS TEXT) FROM center_price_submissions WHERE entry_key=? AND status IN ('approved','rejected') AND decided IS NOT NULL",
            ])
            self.params.extend([key]*4)
            contains="EXISTS (SELECT 1 FROM json_each(entry_keys) WHERE value=?)" if connection.vendor=='sqlite' else 'jsonb_exists(entry_keys::jsonb,?)'
            parts.append("SELECT 'contact-' || id,'contact.updated','CONTACT-' || id,changed,'Center team',field,old_value,new_value FROM center_contact_changes WHERE "+contains)
            self.params.append(key)
        self.sql=' UNION ALL '.join(parts)
    def count(self):
        return self.db.execute('SELECT COUNT(*) AS n FROM ('+self.sql+') AS activity',self.params).fetchone()['n']
    def __getitem__(self, page):
        start=page.start or 0
        rows=self.db.execute('SELECT * FROM ('+self.sql+') AS activity ORDER BY happened DESC,id DESC LIMIT ? OFFSET ?',(*self.params,page.stop-start,start)).fetchall()
        return [{**row,'created_at':datetime.fromtimestamp(float(row['happened']),timezone.utc).isoformat()} for row in rows]
