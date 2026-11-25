from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase, SimpleTestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate
from .models import Center, Membership, BookingRequest
from .integration import sync_requests
from .owner_views import OffersView


class RequestSyncPerformanceTests(TestCase):
    def test_unchanged_requests_use_one_bulk_read_without_decryption(self):
        import time
        center=Center.objects.create(name='Synthetic center',directory_key='synthetic-center')
        BookingRequest.objects.bulk_create([BookingRequest(center=center,legacy_id=f'legacy-{i}',reference=f'legacy-{i}',patient_name='Synthetic',exam='MRI') for i in range(100)])
        rows=[{'id':f'legacy-{i}','status':'requested','closed':None,'link_expires':time.time()+86400} for i in range(100)]
        with patch('center_pro.integration.DomainConnection') as db, patch('booking.unpack') as unpack:
            db.return_value.execute.return_value.fetchall.return_value=rows
            with self.assertNumQueries(1):sync_requests(center)
            unpack.assert_not_called()

    def test_price_reads_do_not_synchronize_unrelated_booking_records(self):
        user=get_user_model().objects.create_user(username='performance-owner')
        center=Center.objects.create(name='Synthetic center',directory_key='synthetic-center')
        Membership.objects.create(user=user,center=center,role='owner')
        request=APIRequestFactory().get('/offers',HTTP_HOST='localhost');force_authenticate(request,user=user)
        with patch('center_pro.integration.sync_requests') as sync:
            response=OffersView.as_view()(request,center_id=center.pk)
        self.assertEqual(response.status_code,200)
        sync.assert_not_called()


class PublicSearchPerformanceTests(SimpleTestCase):
    def test_search_reuses_one_isolated_catalog_snapshot(self):
        from api.views import SearchView
        catalog={'version':'test','entries':[],'offers':[],'locations':[]}
        raw={'location':'National','scope':'state','radius':50,'mod':'','body':'','contrast':'unknown','type':'all','budget':0,'sort':'distance'}
        request=APIRequestFactory().post('/search',raw,format='json');force_authenticate(request,user=SimpleNamespace(is_authenticated=True))
        with patch.object(SearchView,'validated',return_value=raw),patch('core.catalog',return_value=catalog) as load:
            response=SearchView.as_view()(request)
        self.assertEqual(response.status_code,200)
        load.assert_called_once()


class AdminWorkspacePerformanceTests(TestCase):
    def test_membership_counts_are_correct_with_bounded_page_queries(self):
        from .admin_workspace import page
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        user=get_user_model().objects.create_user(username='page-owner')
        centers=Center.objects.bulk_create([Center(name=f'Center {i:02}') for i in range(25)])
        Membership.objects.bulk_create([Membership(user=user,center=c,role='owner' if i<20 else 'staff') for i,c in enumerate(centers)])
        request=APIRequestFactory().get('/admin/center-pro/')
        with CaptureQueriesContext(connection) as queries:
            response=page(SimpleNamespace(each_context=lambda request:{}),request)
        self.assertLessEqual(len(queries),8)
        rows=response.context_data['page_obj']
        self.assertEqual(rows.paginator.count,25)
        self.assertEqual(len(rows.object_list),20)
        self.assertTrue(all(c.owners==1 and c.staff_count==0 for c in rows))
        request=APIRequestFactory().get('/admin/center-pro/',{'page':2})
        response=page(SimpleNamespace(each_context=lambda request:{}),request)
        self.assertTrue(all(c.owners==0 and c.staff_count==1 for c in response.context_data['page_obj']))
