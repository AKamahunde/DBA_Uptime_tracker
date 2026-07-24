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
columns_to_exclude = ['id', 'created_by_id', 'created_at', 'updated_at', 'tested_by']

print("Recreating database views to fix Power BI / Excel connections without the ID column...")

with connection.cursor() as cursor:
    for table in tables:
        view_name = f"vw_{table}"
        try:
            # Get columns for this table
            cursor.execute(f"SHOW COLUMNS FROM {table}")
            columns = [row[0] for row in cursor.fetchall()]
            
            # Filter out the excluded columns so we only keep the original ones
            columns_to_include = [f"`{col}`" for col in columns if col not in columns_to_exclude]
            select_stmt = ", ".join(columns_to_include)
            
            sql = f"CREATE OR REPLACE VIEW {view_name} AS SELECT {select_stmt} FROM {table};"
            cursor.execute(sql)
            print(f"  Success: Recreated view {view_name} (Excluded ID column)")
        except Exception as e:
            print(f"  Error on {view_name}: {e}")

print("\nDone! Please hit Refresh in Power BI / Excel.")
