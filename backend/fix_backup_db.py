import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dba_tracker.settings')
django.setup()

from api.models import BackupRecovery

# The dashboard is looking for 'MySQL', 'Oracle', 'MS SQL Server', or 'PostgreSQL'
valid_dbs = ['MySQL', 'Oracle', 'MS SQL Server', 'PostgreSQL']

# Find all backups that accidentally have their server name saved as the database type
broken_backups = BackupRecovery.objects.exclude(database__in=valid_dbs)

count = broken_backups.count()
print(f"Found {count} broken backup records.")

if count > 0:
    for backup in broken_backups:
        print(f"Fixing {backup.date} - was '{backup.database}'")
        # Since this issue was raised for MySQL, we will default the fix to MySQL
        backup.database = 'MySQL'
        backup.save()
    
    print("All records have been fixed! Your dashboard should now display the latest dates.")
else:
    print("No broken records found.")
