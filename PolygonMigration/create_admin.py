import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "PolygonMigration.settings")
django.setup()

from users.models import User

email = os.getenv("DJANGO_SUPERUSER_EMAIL", "admin@hatsu.com")
password = os.getenv("DJANGO_SUPERUSER_PASSWORD", "admin123")

if not User.objects.filter(email=email).exists():
    User.objects.create_superuser(email=email, password=password)
    print(f"Superuser {email} created successfully.")
else:
    print(f"Superuser {email} already exists.")
