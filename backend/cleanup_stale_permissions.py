import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dba_tracker.settings')
django.setup()

from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.models import Permission

# Find the stale content type for the old model
try:
    ct = ContentType.objects.get(app_label='api', model='availabilitycalculator')
    
    # Delete all associated permissions first
    Permission.objects.filter(content_type=ct).delete()
    print("Deleted stale permissions for AvailabilityCalculator.")
    
    # Delete the content type itself
    ct.delete()
    print("Deleted stale ContentType for AvailabilityCalculator.")
    
except ContentType.DoesNotExist:
    print("AvailabilityCalculator content type already gone.")

print("Cleanup complete!")
