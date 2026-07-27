import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dba_tracker.settings')
django.setup()

from api.models import IncidentRegister

print("Querying latest incidents:")
for inc in IncidentRegister.objects.all().order_by('-created_at')[:5]:
    print(f"Ticket: {inc.ticket}, Created By: {inc.created_by}, Created At: {inc.created_at}")
