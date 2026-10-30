from django.urls import path, re_path
from api import views
urlpatterns = [path('api/health', views.health), re_path(r'^api/(?P<route>.*)$', views.dispatch)]
