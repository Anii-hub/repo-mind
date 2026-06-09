import os
from pathlib import Path

# Bug 7: Load .env file so GROQ_API_KEY and other secrets are available locally
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; fall back to real env vars

BASE_DIR = Path(__file__).resolve().parent.parent

_SECRET_KEY_FALLBACK = "dev-only-secret-key-change-me"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", _SECRET_KEY_FALLBACK)
DEBUG = os.getenv("DJANGO_DEBUG", "True") == "True"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")

# Guard: never run production with the insecure fallback secret key
if not DEBUG and SECRET_KEY == _SECRET_KEY_FALLBACK:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a strong random value in production "
        "(DJANGO_DEBUG=False). Set it in your .env file or environment."
    )


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # Must be right after SecurityMiddleware
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "repomind.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "repomind.wsgi.application"


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"  # Required for collectstatic in production
# Django 5+: STATICFILES_STORAGE was replaced by the STORAGES dict
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Bug 13: MEDIA_URL must be absolute (start with /) to avoid broken URLs in production
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# ── File upload memory thresholds ───────────────────────────────────────────
# FILE_UPLOAD_MAX_MEMORY_SIZE: files LARGER than this are streamed to a temp
# file on disk instead of being held in RAM as a BytesIO object. Set to 2 MB
# so any real ZIP (always > 2 MB) goes straight to disk — the ZIP file itself
# never occupies RAM during the upload. Processing memory is bounded by
# MAX_FILE_BYTES (500 KB/file), not the ZIP size, so large ZIPs are safe.
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024    # 2 MB — stream files to disk

# DATA_UPLOAD_MAX_MEMORY_SIZE only limits non-file POST fields (form text).
# File data is NOT counted here, so this can stay small.
DATA_UPLOAD_MAX_MEMORY_SIZE = 4 * 1024 * 1024    # 4 MB — ample for form fields
DATA_UPLOAD_MAX_NUMBER_FIELDS = 100  # safety guard

# Bug 17: Security cookie flags — use secure cookies in production (non-DEBUG)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# Allow Render's auto-generated domain and any custom domain set via env var
_trusted_origins = os.getenv("CSRF_TRUSTED_ORIGINS", "")
CSRF_TRUSTED_ORIGINS = [o.strip() for o in _trusted_origins.split(",") if o.strip()]

# Bug 16: Structured logging for all service modules
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "core": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}
