from django.urls import path, re_path
from django.conf import settings
from django.views.generic import RedirectView
from django.views.static import serve
from api.admin import admin_site
from api import views
from api.spatial_views import SpatialBundleView

urlpatterns = [
    path('admin', RedirectView.as_view(url='/admin/', permanent=False)),
    path('admin/', admin_site.urls),
    path('admin-assets/<path:path>', serve, {'document_root': settings.STATIC_ROOT}),
    path('api/v1/spatial-bundle/<str:h3_res7>/', SpatialBundleView.as_view(), name='spatial-bundle'),
    path('api/health', views.HealthView.as_view(), name='health'),
    re_path(r'^api/center/(?P<action>code|verify|dashboard|request|logout)$', views.CenterPortalView.as_view(), name='center-portal'),
    path('api/directory-summary', views.DirectorySummaryView.as_view(), name='directory-summary'),
    path('api/guest', views.GuestView.as_view(), name='guest'),
    re_path(r'^api/registration/mobile/(?P<action>code|verify)$', views.RegistrationMobileView.as_view()),
    path('api/account/setup', views.AccountSetupView.as_view()),
    path('api/register', views.RegistrationView.as_view(), name='register'),
    path('api/login', views.AuthenticationView.as_view(), name='login'),
    path('api/logout', views.LogoutView.as_view(), name='logout'),
    path('api/password', views.PasswordView.as_view(), name='password'),
    path('api/dashboard', views.DashboardView.as_view(), name='dashboard'),
    path('api/me', views.MeView.as_view(), name='me'),
    path('api/catalog', views.CatalogView.as_view(), name='catalog'),
    path('api/search', views.SearchView.as_view(), name='search'),
    path('api/agent', views.AgentView.as_view(), name='agent'),
    path('api/guest-usage', views.GuestUsageView.as_view(), name='guest-usage'),
    path('api/conversations', views.ConversationsView.as_view(), name='conversations'),
    path('api/conversations/<str:ident>', views.ConversationDetailView.as_view(), name='conversation-detail'),
    path('api/preferences', views.PreferencesView.as_view(), name='preferences'),
    path('api/corrections', views.CorrectionsView.as_view(), name='corrections'),
    re_path(r'^api/booking/(?P<action>options|email-code|email-verify|phone-code|phone-verify|create|patient|code|verify|details|status|lock)$', views.BookingView.as_view(), name='booking-action'),
    re_path(r'^api/.*$', views.NotFoundView.as_view(), name='api-not-found'),
]
