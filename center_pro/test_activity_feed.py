import json,time
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory,force_authenticate
from .models import Center,Membership,AuditEvent
from .integration import DomainConnection
from .views import AuditView

class CenterActivityFeedTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='history-owner')
        self.center=Center.objects.create(name='History center',directory_key='history-center')
        Membership.objects.create(user=self.user,center=self.center,role='owner')
        db=DomainConnection();now=time.time()
        from pathlib import Path
        for name in ('center-profile.sql','center-prices.sql','center-contact.sql'):
            for sql in (Path(__file__).resolve().parents[1]/'ops'/name).read_text().split(';'):
                if sql.strip():db.execute(sql)
        for key in ('history-center','unrelated-center'):
            db.execute('INSERT INTO center_profile_edits(id,entry_key,email_hash,field,value,old_value,submitted,status,decided,decided_by) VALUES(?,?,?,?,?,?,?,?,?,?)',(key,key,'synthetic','zip','44122','45123',now-30,'approved',now-20,'admin'))
            db.execute('INSERT INTO center_price_submissions(id,entry_key,email_hash,exam,amount_cents,price_type,contrast,interpretation_included,submitted,status,decided) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(key,key,'synthetic','MRI',40000,'fixed','without','yes',now-15,'rejected',now-10))
            db.execute('INSERT INTO center_contact_changes VALUES(?,?,?,?,?,?,?,?)',(key,'phone','111','222',json.dumps([key]),'new','old',now-5))
        AuditEvent.objects.create(center=self.center,actor=self.user,action='access.directory_owner',reference='MEMBER-1')
    def fetch(self,page=1,user=None):
        request=APIRequestFactory().get('/audit',{'page':page},HTTP_HOST='localhost');force_authenticate(request,user=user or self.user)
        return AuditView.as_view()(request,center_id=self.center.pk)
    def test_profile_decisions_prices_contacts_and_access_share_newest_first_timeline(self):
        response=self.fetch();self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['count'],6)
        rows=response.data['results']+self.fetch(2).data['results']
        self.assertEqual([r['action'] for r in rows],['access.directory_owner','contact.updated','price.rejected','price.submitted','profile.approved','profile.submitted'])
        self.assertEqual(len({r['id'] for r in rows}),6)
        self.assertFalse(any('unrelated-center' in r['reference'] for r in rows))
        approved=next(r for r in rows if r['action']=='profile.approved')
        self.assertEqual((approved['old_value'],approved['new_value']),('45123','44122'))
        self.assertEqual(approved['performed_by'],'Administrator')
        self.assertEqual(rows[0]['performed_by'],'System')
    def test_unassigned_user_cannot_read_another_centers_history(self):
        other=get_user_model().objects.create_user(username='unassigned')
        self.assertEqual(self.fetch(user=other).status_code,404)
    def test_unmapped_center_keeps_its_workspace_activity(self):
        self.center.directory_key=None;self.center.save(update_fields=['directory_key'])
        response=self.fetch()
        self.assertEqual(response.data['count'],1)
        self.assertEqual(response.data['results'][0]['reference'],'MEMBER-1')
