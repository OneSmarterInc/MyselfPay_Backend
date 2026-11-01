from django.urls import path, re_path
from api import views
from api.spatial_views import SpatialBundleView

urlpatterns = [
    re_path(r'^api/v1/spatial-bundle/(?P<h3_res7>[^/]+)/?$', SpatialBundleView.as_view(), name='spatial-bundle'),
    path('api/health', views.HealthView.as_view(), name='health'),
    path('api/directory-summary', views.DirectorySummaryView.as_view(), name='directory-summary'),
    path('api/guest', views.GuestView.as_view(), name='guest'),
    path('api/register', views.RegistrationView.as_view(), name='register'),
    path('api/login', views.AuthenticationView.as_view(), name='login'),
    path('api/logout', views.LogoutView.as_view(), name='logout'),
    path('api/password', views.PasswordView.as_view(), name='password'),
    path('api/me', views.MeView.as_view(), name='me'),
    path('api/catalog', views.CatalogView.as_view(), name='catalog'),
    path('api/search', views.SearchView.as_view(), name='search'),
    path('api/agent', views.AgentView.as_view(), name='agent'),
    path('api/guest-usage', views.GuestUsageView.as_view(), name='guest-usage'),
    path('api/conversations', views.ConversationsView.as_view(), name='conversations'),
    path('api/conversations/<str:ident>', views.ConversationDetailView.as_view(), name='conversation-detail'),
    path('api/preferences', views.PreferencesView.as_view(), name='preferences'),
    path('api/corrections', views.CorrectionsView.as_view(), name='corrections'),
    re_path(r'^api/booking/(?P<action>options|email-code|email-verify|create|patient|code|verify|details|status|lock)$', views.BookingView.as_view(), name='booking-action'),
    re_path(r'^api/.*$', views.NotFoundView.as_view(), name='api-not-found'),
]
