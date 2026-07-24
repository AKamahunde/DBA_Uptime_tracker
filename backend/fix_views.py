import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dba_tracker.settings')
django.setup()

from django.db import connection

tables = [
    'incident_register',
    'replication_monitor',
    'backup_recovery',
    'server_health',
    'maintenance_register',
    'capacity_planning',
    'availability_calculator'
]

print("Recreating database views to fix Power BI / Excel connections...")

with connection.cursor() as cursor:
    for table in tables:
        view_name = f"vw_{table}"
        try:
            sql = f"CREATE OR REPLACE VIEW {view_name} AS SELECT * FROM {table};"
            cursor.execute(sql)
            print(f"  Success: Recreated view {view_name}")
        except Exception as e:
            print(f"  Error on {view_name}: {e}")

print("\nDone! Please hit Refresh in Power BI / Excel.")
