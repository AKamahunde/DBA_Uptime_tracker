import os
import django
import sys

# Setup Django environment
sys.path.append('/opt/Uptime_tracker/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dba_tracker.settings')
django.setup()

from api.models import CapacityPlanning

# 1. Oracle August 2026 record
oracle_recs = CapacityPlanning.objects.filter(database='Oracle')
for r in oracle_recs:
    if float(r.database_size_gb) > 1300.0:
        r.database_size_gb = 1022.93
        r.save()
        print(f"Fixed Oracle {r.month} record database size to 1022.93 GB")

# 2. MySQL records with 0 available capacity but 20% disk used
mysql_recs = CapacityPlanning.objects.filter(database='MySQL')
for r in mysql_recs:
    if float(r.available_capacity_gb) == 0.00 and float(r.disk_used_percent) == 20.00:
        r.available_capacity_gb = 298.40
        r.save()
        print(f"Fixed MySQL {r.month} record free space to 298.40 GB")

print("Capacity data cleanup check completed!")
