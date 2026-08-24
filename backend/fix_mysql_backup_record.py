import os
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dba_tracker.settings')
django.setup()

from api.models import BackupRecovery

def main():
    print("Locating MySQL Full Backup for 22 Aug 2026...")
    records = BackupRecovery.objects.filter(
        database='MySQL',
        date='2026-08-22',
        backup_type='Full'
    )
    
    if not records.exists():
        print("No record found matching the criteria.")
        return
        
    for r in records:
        print(f"Found Record ID: {r.id}")
        print(f"  Old Reference: {repr(r.backup_used)}")
        print(f"  Old Compression: {r.compression}")
        
        # Update values to correct compression and filename
        r.compression = True
        r.backup_used = "nickledb_2026-08-22_22-00-00_full.tar.gz"
        r.save()
        
        print(f"  New Reference: {repr(r.backup_used)}")
        print(f"  New Compression: {r.compression}")
        print("Record updated successfully.")

if __name__ == '__main__':
    main()
