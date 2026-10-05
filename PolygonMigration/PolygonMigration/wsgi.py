"""
WSGI config for PolygonMigration project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "PolygonMigration.settings")

application = get_wsgi_application()

try:
    from users.models import User
    email = os.getenv("DJANGO_SUPERUSER_EMAIL", "admin@hatsu.com")
    password = os.getenv("DJANGO_SUPERUSER_PASSWORD", "admin123")
    if not User.objects.filter(is_staff=True).exists():
        User.objects.create_superuser(email=email, password=password)
except Exception:
    pass

