import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dba_tracker.settings')
django.setup()

from users.models import User

# Create superuser
username = 'admin'
password = 'admin123'
email = 'admin@example.com'

if not User.objects.filter(username=username).exists():
    user = User.objects.create_superuser(
        username=username,
        email=email,
        password=password
    )
    user.is_admin = True
    user.save()
    print(f"Superuser '{username}' created successfully with is_admin=True")
else:
    print(f"Superuser '{username}' already exists")
