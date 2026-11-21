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
