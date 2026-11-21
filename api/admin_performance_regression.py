"""Query budgets for admin rendering; no external mail or production data."""
from unittest.mock import patch, MagicMock
from django.test import TransactionTestCase, SimpleTestCase, RequestFactory
from django.db import connection
from django.contrib.admin import AdminSite
from django.core.cache import cache
from api.models import PortalAccount, PortalMember, PortalSetting
from api.admin import PortalAccountAdmin
import admin_alerts


class UserListQueryTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as editor:
            for model in (PortalAccount, PortalMember, PortalSetting):
                editor.create_model(model)
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE mobile_accounts(member_id TEXT PRIMARY KEY,username TEXT)')

    @classmethod
    def tearDownClass(cls):
        with connection.cursor() as cursor:
            cursor.execute('DROP TABLE mobile_accounts')
        with connection.schema_editor() as editor:
            for model in (PortalSetting, PortalMember, PortalAccount):
                editor.delete_model(model)
        super().tearDownClass()

    def test_fifty_users_render_all_columns_in_one_query(self):
        for n in range(50):
            ident=str(n)
            PortalAccount.objects.create(member_id=ident,email='',phone='+15550000000',created=n)
            PortalMember.objects.create(id=ident,tenant='selfpay',email='',name='Name '+ident,salt='',password_hash='')
            if n % 2 == 0:
                PortalSetting.objects.create(key='account-disabled:'+ident,value='1')
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO mobile_accounts VALUES(%s,%s)',[ident,'user'+ident])
        model_admin=PortalAccountAdmin(PortalAccount,AdminSite())
        with self.assertNumQueries(1):
            rows=list(model_admin.get_queryset(RequestFactory().get('/admin/')).order_by('created')[:50])
            rendered=[(model_admin.username(row),model_admin.user_name(row),model_admin.account_status(row)) for row in rows]
        self.assertEqual(rendered[0],('user0','Name 0','Suspended'))
        self.assertEqual(rendered[1],('user1','Name 1','Active'))


class SidebarQueryTests(SimpleTestCase):
    def test_counts_reused_only_for_short_cache_window(self):
        cache.clear()
        db=MagicMock()
        db.execute.return_value.fetchone.side_effect=[{'n':3},{'n':4}]
        with patch('admin_alerts.booking.connection') as connect:
            connect.return_value.__enter__.return_value=db
            self.assertEqual(admin_alerts.pending_counts('synthetic-db'),(3,4))
            self.assertEqual(admin_alerts.pending_counts('synthetic-db'),(3,4))
            self.assertEqual(connect.call_count,1)
            cache.clear()
        
    def test_database_failure_is_not_cached(self):
        cache.clear()
        with patch('admin_alerts.booking.connection',side_effect=RuntimeError('unavailable')) as connect:
            self.assertEqual(admin_alerts.pending_counts('failed-db'),(0,0))
            self.assertEqual(admin_alerts.pending_counts('failed-db'),(0,0))
            self.assertEqual(connect.call_count,2)

    def test_reply_count_has_short_cache_without_mailbox_network(self):
        cache.clear()
        with patch('admin_mailer.mailer_contacts') as count:
            count.return_value.count.return_value=7
            self.assertEqual(admin_alerts.reply_count(),7)
            self.assertEqual(admin_alerts.reply_count(),7)
            self.assertEqual(count.call_count,1)
        cache.clear()

    def test_notification_markup_and_sidebar(self):
        from django.template.loader import render_to_string
        html=render_to_string('admin/base_site.html',{'has_permission':True,'pending_prices':5,
            'received_replies':2,'notification_count':7,'user':MagicMock(),'request':RequestFactory().get('/admin/')})
        self.assertIn('Prices awaiting approval',html)
        self.assertIn('Replies received',html)
        self.assertIn('?audience=replied',html)
        self.assertIn('Notifications: 7',html)
        nav=html.split('<nav id="sp-admin-navigation"')[1].split('</nav>')[0]
        self.assertNotIn('Updates waiting for approval',nav)
        self.assertNotIn('sp-nav-count',nav)
        self.assertIn('Center prices',nav)
        Path=__import__('pathlib').Path
        Path('data/notification-preview.html').write_text(html,encoding='utf-8')


class ReplyNotificationConsistencyTests(__import__('django.test',fromlist=['TestCase']).TestCase):
    def test_multiple_replies_and_hidden_contacts_match_visible_center_list(self):
        from api.models import MailerContact, MailerCampaign, MailerMessage, MailerReply, MailerSuppression
        from django.utils import timezone
        from admin_mailer import mailer_contacts
        now=timezone.now()
        campaign=MailerCampaign.objects.create(subject='Synthetic',body='Test',selection='1-3',reply_to='admin@example.test',created_by='admin')
        for n in range(3):
            contact=MailerContact.objects.create(center_key=str(n),email=f'center{n}@example.test',imported_at=now,active=n!=2)
            message=MailerMessage.objects.create(campaign=campaign,contact=contact,email=contact.email,center_names='Test',position=n+1,message_id=f'synthetic-{n}')
            for index in range(3):
                MailerReply.objects.create(message=message,identity=f'{n}-{index}',sender=contact.email,subject='Test',body_encrypted='synthetic',received_at=now)
        MailerSuppression.objects.create(email='center1@example.test')
        cache.clear()
        self.assertEqual(MailerReply.objects.count(),9)
        self.assertEqual(mailer_contacts(audience='replied').count(),1)
        self.assertEqual(admin_alerts.reply_count(),1)
        cache.clear()
