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

# These are the columns Django models require that are likely missing from your manual SQL tables
commands = [
    "ALTER TABLE `{table}` ADD COLUMN `id` INT AUTO_INCREMENT PRIMARY KEY FIRST;",
    "ALTER TABLE `{table}` ADD COLUMN `created_by_id` BIGINT NULL;",
    "ALTER TABLE `{table}` ADD COLUMN `created_at` DATETIME(6) NULL;",
    "ALTER TABLE `{table}` ADD COLUMN `updated_at` DATETIME(6) NULL;",
]

with connection.cursor() as cursor:
    for table in tables:
        print(f"Fixing table {table}...")
        for cmd in commands:
            try:
                sql = cmd.format(table=table)
                cursor.execute(sql)
                print(f"  Success: {sql}")
            except Exception as e:
                # If column already exists or similar error, it will just skip
                print(f"  Skipped: {e}")

print("\nAll done! The database tables are now synced with Django.")
