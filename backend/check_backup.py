import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dba_tracker.settings')
django.setup()

from api.models import BackupRecovery

print("Querying BackupRecovery records for 2026-07-02:")
records = BackupRecovery.objects.filter(date="2026-07-02")
if not records.exists():
    print("No records found for this date.")
else:
    for r in records:
        print(f"ID: {r.id}")
        print(f"  Record Type: {repr(r.record_type)}")
        print(f"  Database: {repr(r.database)}")
        print(f"  Date: {repr(r.date)}")
        print(f"  Instance: {repr(r.database_instance)}")
        print(f"  Backup Type: {repr(r.backup_type)}")
        print(f"  Backup Used: {repr(r.backup_used)}")
        print(f"  Start: {repr(r.start)}")
        print(f"  End: {repr(r.end)}")
        print(f"  Duration Mins: {repr(r.duration_mins)}")
        print(f"  Outcome: {repr(r.outcome)}")
        print(f"  Location: {repr(r.location)}")
        print(f"  Verified: {repr(r.verified)}")
        print(f"  Restore Tested: {repr(r.restore_tested)}")
        print(f"  Compression: {repr(r.compression)}")
        print(f"  Tested By: {repr(r.tested_by)}")
        print(f"  Comments: {repr(r.comments)}")
        print("-" * 40)
