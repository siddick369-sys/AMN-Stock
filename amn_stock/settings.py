import dj_database_url
from pathlib import Path
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Clés & mode ────────────────────────────────────────────────────────────────
SECRET_KEY = config('SECRET_KEY')                        # obligatoire — pas de fallback
DEBUG       = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv())

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'widget_tweaks',
    'stock',
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

ROOT_URLCONF = 'amn_stock.urls'

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

WSGI_APPLICATION = 'amn_stock.wsgi.application'

# ── Base de données ────────────────────────────────────────────────────────────
# En dev (sans DATABASE_URL dans .env) → SQLite local.
# En prod (Render / NeonDB) → lire DATABASE_URL depuis les env vars.
# ⚠️  Le ?sslmode=require doit être dans l'URL NeonDB — ne pas utiliser ssl_require
#     car il injecte sslmode dans OPTIONS pour TOUS les backends (y compris SQLite).
_DATABASE_URL = config('DATABASE_URL', default=f'sqlite:///{BASE_DIR / "db.sqlite3"}')
_IS_POSTGRES   = _DATABASE_URL.startswith(('postgres://', 'postgresql://'))

DATABASES = {
    'default': dj_database_url.parse(
        _DATABASE_URL,
        conn_max_age=60,
        conn_health_checks=True,
    )
}

# PgBouncer transaction mode (NeonDB) : pas de server-side cursors
if _IS_POSTGRES:
    DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'] = True

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'fr-fr'
TIME_ZONE     = 'Africa/Abidjan'
USE_I18N = True
USE_TZ   = True

STATIC_URL    = '/static/'
STATIC_ROOT   = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL           = '/login/'
LOGIN_REDIRECT_URL  = '/'
LOGOUT_REDIRECT_URL = '/login/'

# ── Email (Brevo SMTP) ─────────────────────────────────────────────────────────
EMAIL_BACKEND       = config('EMAIL_BACKEND', default='django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST          = config('EMAIL_HOST',     default='smtp-relay.brevo.com')
EMAIL_PORT          = config('EMAIL_PORT',     default=2525, cast=int)
EMAIL_USE_TLS       = config('EMAIL_USE_TLS',  default=True, cast=bool)
EMAIL_HOST_USER     = config('EMAIL_HOST_USER',     default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL  = config('DEFAULT_FROM_EMAIL',  default='noreply@amn.africa')
# Liste d'adresses séparées par des virgules : admin@amn.africa,autre@amn.africa
ADMIN_EMAIL         = config('ADMIN_EMAIL', default='', cast=Csv())

# ── Cache (LocMemCache — thread-safe, single worker) ───────────────────────────
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "unique-snowflake",
    }
}

# ── Cron Pseudo-Worker ─────────────────────────────────────────────────────────
# Token protégeant le webhook /tasks/trigger-celery/
# Générer : python -c "import secrets; print(secrets.token_hex(32))"
CRON_TRIGGER_TOKEN = config('CRON_TRIGGER_TOKEN', default='')

# ── Groq AI ────────────────────────────────────────────────────────────────────
GROQ_API_KEY = config('GROQ_API_KEY', default='')

# ── Green API (WhatsApp) ───────────────────────────────────────────────────────
GREENAPI_INSTANCE_ID = config('GREENAPI_INSTANCE_ID', default='')
GREENAPI_TOKEN       = config('GREENAPI_TOKEN',       default='')
# Numéro cible sans '+', ex: 237678317658 pour +237 678 317 658
GREENAPI_RECIPIENT   = config('GREENAPI_RECIPIENT',   default='')
GREENAPI_BASE_URL    = config('GREENAPI_BASE_URL',    default='https://api.green-api.com')

# ── Sécurité HTTPS (prod uniquement, DEBUG=False) ──────────────────────────────
if not DEBUG:
    SECURE_PROXY_SSL_HEADER     = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT         = True
    SESSION_COOKIE_SECURE       = True
    CSRF_COOKIE_SECURE          = True
    SECURE_HSTS_SECONDS         = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD         = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    CSRF_TRUSTED_ORIGINS        = config('CSRF_TRUSTED_ORIGINS', default='', cast=Csv())
