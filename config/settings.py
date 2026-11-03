import os
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')
DEBUG = os.getenv('DJANGO_DEBUG', 'false').lower() == 'true'
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured('Set DJANGO_SECRET_KEY before starting production.')
    SECRET_KEY = 'development-only-selfpay-key-do-not-use-in-production'
ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
FRONTEND_ORIGIN = os.getenv('FRONTEND_ORIGIN', 'http://localhost:5173').rstrip('/')
parsed_origin = urlparse(FRONTEND_ORIGIN)
if parsed_origin.path or not parsed_origin.netloc or parsed_origin.scheme not in ('http', 'https'):
    raise ImproperlyConfigured('FRONTEND_ORIGIN must be an origin without a path.')
if not DEBUG and parsed_origin.scheme != 'https':
    raise ImproperlyConfigured('Production FRONTEND_ORIGIN must use HTTPS.')
DOMAIN_DATABASE = os.getenv('DATABASE_URL', '')
if not DOMAIN_DATABASE:
    if not DEBUG:
        raise ImproperlyConfigured('Production requires a PostgreSQL DATABASE_URL.')
    DOMAIN_DATABASE = str(BASE_DIR / 'data' / 'development.sqlite3')
    Path(DOMAIN_DATABASE).parent.mkdir(parents=True, exist_ok=True)
if DOMAIN_DATABASE.startswith(('postgres://', 'postgresql://')):
    DATABASES = {'default': dj_database_url.parse(DOMAIN_DATABASE, conn_max_age=60)}
    DATABASES['default']['OPTIONS'] = {'sslmode': 'require', 'prepare_threshold': None}
    requested_ssl = parse_qs(urlparse(DOMAIN_DATABASE).query).get('sslmode', ['require'])[-1]
    if requested_ssl not in ('require', 'verify-ca', 'verify-full') and urlparse(DOMAIN_DATABASE).hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ImproperlyConfigured('Remote PostgreSQL requires TLS.')
    DATABASES['default']['OPTIONS']['sslmode'] = requested_ssl
    if urlparse(DOMAIN_DATABASE).hostname in ('localhost', '127.0.0.1', '::1'):
        DATABASES['default']['OPTIONS']['sslmode'] = 'disable'
else:
    if not DEBUG:
        raise ImproperlyConfigured('Production requires PostgreSQL.')
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': DOMAIN_DATABASE}}
INSTALLED_APPS = ['django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'corsheaders', 'rest_framework', 'api']
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['api.authentication.SelfPaySessionAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_PARSER_CLASSES': ['rest_framework.parsers.JSONParser'],
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
    'EXCEPTION_HANDLER': 'api.exceptions.api_exception_handler',
    'UNAUTHENTICATED_USER': None,
    'UNAUTHENTICATED_TOKEN': None,
}
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'api.middleware.RequestSecurityMiddleware',
]
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
APPEND_SLASH = False
DATA_UPLOAD_MAX_MEMORY_SIZE = 100000
CORS_ALLOWED_ORIGINS = [FRONTEND_ORIGIN]
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = ['content-type', 'x-csrf-token', 'x-center-csrf']
CORS_ALLOW_METHODS = ['GET', 'POST', 'DELETE', 'OPTIONS']
SELFPAY_SESSION_COOKIE_NAME = 'selfpay_session' if DEBUG else '__Host-selfpay_session'
SESSION_COOKIE_NAME = 'selfpay_admin_session' if DEBUG else '__Host-selfpay_admin_session'
SELFPAY_COOKIE_SAMESITE = os.getenv('COOKIE_SAMESITE', 'Lax')
if SELFPAY_COOKIE_SAMESITE not in ('Strict', 'Lax', 'None') or (DEBUG and SELFPAY_COOKIE_SAMESITE == 'None'):
    raise ImproperlyConfigured('COOKIE_SAMESITE must be Strict/Lax, or None with production HTTPS.')
SECURE_SSL_REDIRECT = not DEBUG
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'no-referrer'
X_FRAME_OPTIONS = 'DENY'
SECURE_REDIRECT_EXEMPT = [r'^api/health/?$']
LOGGING = {'version': 1, 'disable_existing_loggers': False,
           'handlers': {'console': {'class': 'logging.StreamHandler'}},
           'loggers': {'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False}}}

TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates',
              'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True,
              'OPTIONS': {'context_processors': ['django.template.context_processors.request',
                         'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
STATIC_URL = '/admin-assets/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
CSRF_TRUSTED_ORIGINS = [FRONTEND_ORIGIN]
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 3600
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_SAMESITE = 'Lax'
AUTH_PASSWORD_VALIDATORS = [
 {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
 {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
 {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
 {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
