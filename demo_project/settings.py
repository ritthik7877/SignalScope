"""Settings for the SignalScope demonstration project."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = "signalscope-demo-only"
DEBUG = True
ALLOWED_HOSTS = ["*"]
ROOT_URLCONF = "demo_project.urls"
MIDDLEWARE = []
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "signalscope.apps.SignalScopeConfig",
    "demo_project.catalog.apps.CatalogConfig",
    "demo_project.orders.apps.OrdersConfig",
    "demo_project.workflows.apps.WorkflowsConfig",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SIGNALSCOPE = {"MAX_QUERIES": 5, "SLOW_MS": 100, "MAX_DEPTH": 3, "ENABLED": DEBUG}
# This unresolved sender is intentional in the demo and is reported by SignalScope.
SILENCED_SYSTEM_CHECKS = ["signals.E001"]
