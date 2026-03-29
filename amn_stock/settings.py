import os
import dj_database_url
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ['SECRET_KEY']  # Obligatoire en prod — pas de fallback

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_celery_beat',
    'django_celery_results',
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

# PostgreSQL via DATABASE_URL (NeonDB pooled — PgBouncer transaction mode)
# En local sans DATABASE_URL → fallback SQLite pour le dev
# ⚠️  ssl_require ne doit PAS être utilisé ici : il injecte sslmode dans
#     OPTIONS pour TOUS les backends, y compris SQLite → TypeError au build.
#     Le ?sslmode=require est déjà inclus dans l'URL NeonDB et parsé par
#     dj_database_url → OPTIONS: {'sslmode': 'require'} côté PostgreSQL seulement.
_DATABASE_URL = os.environ.get('DATABASE_URL', f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
_IS_POSTGRES = _DATABASE_URL.startswith(('postgres://', 'postgresql://'))

DATABASES = {
    'default': dj_database_url.config(
        default=_DATABASE_URL,
        conn_max_age=0,          # 0 obligatoire avec PgBouncer transaction mode (NeonDB)
        conn_health_checks=True,
    )
}

# DISABLE_SERVER_SIDE_CURSORS uniquement pour PostgreSQL :
# PgBouncer transaction mode ne supporte pas les named cursors (Paginator, iterator()).
# Ne pas appliquer à SQLite (clé ignorée mais propre de ne pas polluer la config).
if _IS_POSTGRES:
    DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'] = True

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'Africa/Abidjan'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# Email configuration
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@amn.africa')
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'admin@amn.africa').split(',')

# Celery configuration
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
# Configuration SSL pour Upstash (rediss://)
CELERY_BROKER_USE_SSL = {
    'ssl_cert_reqs': None  # Désactive la vérification du certificat pour Upstash mode bridge
} if CELERY_BROKER_URL.startswith('rediss://') else None

CELERY_RESULT_BACKEND = 'django-db'
CELERY_CACHE_BACKEND = 'django-cache'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

# Cache configuration (shared between Web and Celery)
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": CELERY_BROKER_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "CONNECTION_POOL_KWARGS": {
                "ssl_cert_reqs": None
            } if CELERY_BROKER_URL.startswith('rediss://') else {}
        }
    }
}

# Low stock threshold
LOW_STOCK_THRESHOLD = int(os.environ.get('LOW_STOCK_THRESHOLD', 5))

# ── Cron Pseudo-Worker ──────────────────────────────────────────────────────
# Token secret protégeant le webhook /tasks/trigger-celery/
# Générer avec : python -c "import secrets; print(secrets.token_hex(32))"
# ⚠️  Utiliser uniquement des caractères alphanumériques (hex) pour éviter
#     les problèmes d'encodage URL dans cron-job.org
CRON_TRIGGER_TOKEN = os.environ.get('CRON_TRIGGER_TOKEN', '')

# ── Groq AI ────────────────────────────────────────────────────────────────
# Obtenir la clé sur https://console.groq.com/keys
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# ── Green API (WhatsApp) ────────────────────────────────────────────────────
# Obtenir les credentials sur https://console.green-api.com/
GREENAPI_INSTANCE_ID = os.environ.get("GREENAPI_INSTANCE_ID", "")
GREENAPI_TOKEN       = os.environ.get("GREENAPI_TOKEN", "")
# Numéro WhatsApp cible (sans +), ex: 237678317658 pour +237 678 317 658
GREENAPI_RECIPIENT   = os.environ.get('GREENAPI_RECIPIENT', '237678317658')
# URL de base Green API (ne pas modifier sauf test)
GREENAPI_BASE_URL    = os.environ.get('GREENAPI_BASE_URL', 'https://api.green-api.com')

# ── Sécurité HTTPS (activé uniquement en production, DEBUG=False) ────────────
if not DEBUG:
    # Render place l'application derrière un proxy SSL
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT     = True

    # Cookies sécurisés (HTTPS uniquement)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE    = True

    # HSTS : force le navigateur à utiliser HTTPS pendant 1 an
    SECURE_HSTS_SECONDS                = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS     = True
    SECURE_HSTS_PRELOAD                = True

    # Domaines de confiance pour les requêtes CSRF (remplacer par votre domaine Render)
    CSRF_TRUSTED_ORIGINS = os.environ.get(
        'CSRF_TRUSTED_ORIGINS', ''
    ).split(',')

    # Empêche le sniffing de type MIME
    SECURE_CONTENT_TYPE_NOSNIFF = True
