from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv(os.getenv("DOTENV_PATH", ".env"))

BASE_DIR = Path(__file__).resolve().parent.parent

# ----------------------------
# Core security / environment
# ----------------------------

DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not DEBUG and not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY must be set in production")

# Allowed hosts: comma-separated env, e.g. "example.com,api.example.com"
_allowed = os.getenv("DJANGO_ALLOWED_HOSTS", "")
if _allowed.strip():
    ALLOWED_HOSTS = [h.strip() for h in _allowed.split(",") if h.strip()]
else:
    # safe local defaults
    ALLOWED_HOSTS = ["localhost", "127.0.0.1", '*']

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    'accounts',
    'main',

    "rest_framework",
    "channels",
    # "voice",
    'strawberry.django',

]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "main.middleware.JWTAuthenticationMiddleware"
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ----------------------------
# Channels (dev vs prod)
# ----------------------------
# For production, prefer Redis:
# CHANNEL_BACKEND=redis and REDIS_URL=redis://host:6379/0
CHANNEL_BACKEND = os.getenv("CHANNEL_BACKEND", "inmemory").lower()

if CHANNEL_BACKEND == "redis":
    REDIS_URL = os.getenv("REDIS_URL", "")
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [REDIS_URL]},
        }
    }
else:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# ----------------------------
# Database
# ----------------------------
# Keep sqlite by default; can switch to Postgres later by changing env vars.
DATABASES = {
    "default": {
        "ENGINE": os.getenv("DJANGO_DB_ENGINE", "django.db.backends.sqlite3"),
        "NAME": os.getenv("DJANGO_DB_NAME", str(BASE_DIR / "db.sqlite3")),
    }
}

# ----------------------------
# Internationalization
# ----------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Dhaka"
USE_I18N = True
USE_TZ = True

# ----------------------------
# Static / Media
# ----------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ----------------------------
# DRF (optional defaults)
# ----------------------------
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
    )
}
AUTH_USER_MODEL = "accounts.User"

# Gmail SMTP settings (for OTP emails)
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "1") == "1"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER
# ----------------------------
# Voice / AI Settings
# ----------------------------
VOICE_APP = {
    # Vector DB provider (switch later without rewriting code)
    # "chroma" now, "pinecone" later
    "VECTOR_DB": os.getenv("VECTOR_DB", "chroma"),

    "LLM_PROVIDER": os.getenv("LLM_PROVIDER", "openai"),
    "STT_PROVIDER": os.getenv("STT_PROVIDER", "openai"),
    "TTS_PROVIDER": os.getenv("TTS_PROVIDER", "openai"),

    # Model configs
    "OPENAI_LLM_MODEL": os.getenv("OPENAI_LLM_MODEL", "gpt-5.2-chat-latest"),
    "OPENAI_STT_MODEL": os.getenv("OPENAI_STT_MODEL", "gpt-4o-transcribe"),
    "OPENAI_TTS_MODEL": os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
    "OPENAI_TTS_VOICE": os.getenv("OPENAI_TTS_VOICE", "cedar"),

    # Realtime session options used by consumers.py
    "OPENAI_RT_VOICE": os.getenv("OPENAI_RT_VOICE", "marin"),
    "OPENAI_RT_TRANSCRIBE_MODEL": os.getenv("OPENAI_RT_TRANSCRIBE_MODEL", "gpt-4o-transcribe"),

    # OpenAI Realtime WS URL (env-only)
    "OPENAI_REALTIME_URL": os.getenv("OPENAI_REALTIME_URL", ""),

    "WHISPER_MODEL": os.getenv("WHISPER_MODEL", "base"),

    # Chroma persistence location (only used if VECTOR_DB=chroma)
    "CHROMA_DIR": os.getenv("CHROMA_DIR", str(BASE_DIR / "chroma_db")),

    # API keys
    "GROQ_API_KEY": os.getenv("GROQ_API_KEY", ""),
    "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),

    # ✅ ElevenLabs (voice cloning + streaming TTS)
    "ELEVENLABS_API_KEY": os.getenv("ELEVENLABS_API_KEY", ""),
    "ELEVENLABS_BASE_URL": os.getenv("ELEVENLABS_BASE_URL", ""),
    "ELEVENLABS_DEFAULT_VOICE_ID": os.getenv("ELEVENLABS_DEFAULT_VOICE_ID", ""),
    "ELEVENLABS_MODEL_ID": os.getenv("ELEVENLABS_MODEL_ID", ""),

    # ChatGPT-like memory settings
    "AUTO_MEMORY_ENABLED": os.getenv("AUTO_MEMORY_ENABLED", "1") == "1",
    "OPENAI_MEMORY_MODEL": os.getenv("OPENAI_MEMORY_MODEL", "gpt-4o-mini"),
    "MEMORY_EXTRACT_MAX_ITEMS": int(os.getenv("MEMORY_EXTRACT_MAX_ITEMS", "3")),
    "MEMORY_EXTRACT_MIN_INTERVAL_SEC": float(os.getenv("MEMORY_EXTRACT_MIN_INTERVAL_SEC", "12")),

    # Optional debug override
    "MEMORY_ALWAYS_EXTRACT": os.getenv("MEMORY_ALWAYS_EXTRACT", "0") == "1",
}
