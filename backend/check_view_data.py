import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dba_tracker.settings')
django.setup()

from django.db import connection

print("Querying SQL VIEW vw_backup_recovery for '2026-07-02':")
with connection.cursor() as cursor:
    cursor.execute("SELECT * FROM vw_backup_recovery WHERE `Backup Date` = '2026-07-02'")
    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchall()
    
    if not rows:
        print("No rows found in view for this date.")
    else:
        for row in rows:
            for col, val in zip(columns, row):
                print(f"  {col}: {repr(val)}")
            print("-" * 40)
