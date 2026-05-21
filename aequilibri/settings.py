"""
aequilibri POC — Django Settings
Production-ready for Render (Linux) and local Windows development.
All secrets are read from environment variables / .env — never hard-coded.
"""
import os
from pathlib import Path

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / '.env', override=True)
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent

# ── SECURITY ──────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'CHANGE-THIS-IN-DOT-ENV-before-deploying-abc123xyz'
)

DEBUG = os.environ.get('DJANGO_DEBUG', 'False').lower() in ('1', 'true', 'yes')

_raw_hosts = os.environ.get('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost')
ALLOWED_HOSTS = [h.strip() for h in _raw_hosts.split(',') if h.strip()]

# ── APPLICATIONS ──────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'uc1_roofing',
    'uc2_didi',
    'uc3_msme',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'aequilibri.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'aequilibri.wsgi.application'

# ── DATABASE ──────────────────────────────────────────────────────────────────
# Default: SQLite for local development.
# On Render: set DATABASE_URL to the managed PostgreSQL connection string and
# this block automatically switches to PostgreSQL.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

_database_url = os.environ.get('DATABASE_URL', '')
if _database_url:
    try:
        import dj_database_url
        DATABASES['default'] = dj_database_url.config(
            default=_database_url,
            conn_max_age=600,
            conn_health_checks=True,
        )
    except ImportError:
        pass  # dj-database-url not installed — fall back to SQLite

# ── STATIC FILES ──────────────────────────────────────────────────────────────
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static'] if (BASE_DIR / 'static').exists() else []
STATIC_ROOT = BASE_DIR / 'staticfiles'

STORAGES = {
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── API KEYS ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY    = os.environ.get('ANTHROPIC_API_KEY', '')
GOOGLE_MAPS_API_KEY  = os.environ.get('GOOGLE_MAPS_API_KEY', '')
GOOGLE_SOLAR_API_KEY = os.environ.get('GOOGLE_SOLAR_API_KEY', GOOGLE_MAPS_API_KEY)
GEOSCAPE_CONSUMER_KEY    = os.environ.get('GEOSCAPE_CONSUMER_KEY', '')
GEOSCAPE_CONSUMER_SECRET = os.environ.get('GEOSCAPE_CONSUMER_SECRET', '')

# ── LOCALE ────────────────────────────────────────────────────────────────────
LANGUAGE_CODE = 'en-au'
TIME_ZONE     = 'Australia/Sydney'
USE_I18N = True
USE_TZ   = True

LOGIN_URL = '/admin/login/'

# ── CSRF TRUSTED ORIGINS ──────────────────────────────────────────────────────
# Required for HTTPS POST requests (Render, custom domains).
# Set via env var: CSRF_TRUSTED_ORIGINS=https://yourapp.onrender.com,https://yourdomain.com
_csrf_env = os.environ.get('CSRF_TRUSTED_ORIGINS', '')
if _csrf_env:
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_env.split(',') if o.strip()]
elif not DEBUG:
    CSRF_TRUSTED_ORIGINS = ['https://*.onrender.com']

# ── SECURITY HEADERS (production only) ───────────────────────────────────────
if not DEBUG:
    SECURE_BROWSER_XSS_FILTER  = True
    X_FRAME_OPTIONS             = 'SAMEORIGIN'
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_HTTPONLY     = True
    CSRF_COOKIE_HTTPONLY        = True
